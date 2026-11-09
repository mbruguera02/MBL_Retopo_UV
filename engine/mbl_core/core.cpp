// mbl_core: núcleo nativo del Motor MBL. Etapas S0 (entrada), S1 (análisis), S3 (campo), S4 (parches), S5 (reparto) y S6 (quads).
//
//   mbl_core entrada.obj salida.json [--field campo.bin] [--poles polos.txt] [--patches parches.txt] [--quads quads.obj] [--faces caras] [--memory-mb N] [--feature-angle grados]
//
// Se ejecuta aparte de Maya: si falla, Maya no se cae. Es deliberadamente poco invasivo: prioridad baja, un solo hilo y
// tope de memoria impuesto por el sistema. Determinista: la misma malla da siempre el mismo resultado.
//
// Código propio (licencia MIT, ver LICENSE). El campo sigue las ideas de:
//   Ray, Vallet, Li y Lévy, "N-Symmetry Direction Field Design", ACM TOG 2008 (índices y Poincaré-Hopf).
//   Jakob, Tarini, Panozzo y Sorkine-Hornung, "Instant Field-Aligned Meshes", SIGGRAPH Asia 2015 (suavizado por niveles).

#define NOMINMAX
#include <windows.h>
#include <psapi.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <new>
#include <queue>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

using Complex = std::complex<double>;
static const double PI = 3.14159265358979323846;

struct Vec3 {
    double x = 0, y = 0, z = 0;
    Vec3() {}
    Vec3(double a, double b, double c) : x(a), y(b), z(c) {}
    Vec3 operator+(const Vec3& o) const { return Vec3(x + o.x, y + o.y, z + o.z); }
    Vec3 operator-(const Vec3& o) const { return Vec3(x - o.x, y - o.y, z - o.z); }
    Vec3 operator*(double s) const { return Vec3(x * s, y * s, z * s); }
    double dot(const Vec3& o) const { return x * o.x + y * o.y + z * o.z; }
    Vec3 cross(const Vec3& o) const { return Vec3(y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x); }
    double norm() const { return std::sqrt(dot(*this)); }
};

// ---------------------------------------------------------------- límites: no invasivo

static void limit_resources(size_t memory_mb) {
    SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS);
    MEMORYSTATUSEX status;
    status.dwLength = sizeof(status);
    GlobalMemoryStatusEx(&status);
    // sin tope pedido: como mucho la mitad de la memoria libre ahora mismo
    size_t limit = memory_mb ? memory_mb * 1024 * 1024 : static_cast<size_t>(status.ullAvailPhys / 2);
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = {};
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY;
        info.ProcessMemoryLimit = limit;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info, sizeof(info));
        AssignProcessToJobObject(job, GetCurrentProcess());
    }
}

static double peak_memory_mb() {
    PROCESS_MEMORY_COUNTERS counters;
    if (!GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof(counters))) return 0.0;
    return counters.PeakWorkingSetSize / (1024.0 * 1024.0);
}

// ---------------------------------------------------------------- S0: entrada

struct Mesh {
    std::vector<Vec3> points;
    std::vector<std::array<int, 3>> tris;
    std::vector<std::array<int, 3>> across;  // triángulo al otro lado de cada arista (v[k], v[k+1]); -1 si borde o no-manifold
    std::vector<Vec3> normal, tangent, bitangent;
    std::vector<double> area;
    int edge_count = 0, border_edges = 0, nonmanifold_edges = 0, flipped_pairs = 0, pieces = 0;
};

static bool load_obj(const char* path, Mesh& mesh) {
    FILE* file = fopen(path, "r");
    if (!file) return false;
    char line[65536];
    std::vector<int> face;
    while (fgets(line, sizeof(line), file)) {
        if (line[0] == 'v' && line[1] == ' ') {
            Vec3 p;
            if (sscanf(line + 2, "%lf %lf %lf", &p.x, &p.y, &p.z) == 3) mesh.points.push_back(p);
        } else if (line[0] == 'f' && line[1] == ' ') {
            face.clear();
            char* cursor = line + 2;
            while (*cursor) {
                char* end;
                long index = strtol(cursor, &end, 10);
                if (end == cursor) break;
                face.push_back(static_cast<int>(index < 0 ? static_cast<long>(mesh.points.size()) + index : index - 1));
                cursor = end;
                while (*cursor && *cursor != ' ' && *cursor != '\t') ++cursor;
                while (*cursor == ' ' || *cursor == '\t') ++cursor;
            }
            for (size_t k = 1; k + 1 < face.size(); ++k) mesh.tris.push_back({{face[0], face[k], face[k + 1]}});
        }
    }
    fclose(file);
    return !mesh.tris.empty();
}

static void build_topology(Mesh& mesh) {
    struct Side { int low, high, tri, corner; };
    std::vector<Side> sides;
    sides.reserve(mesh.tris.size() * 3);
    for (int t = 0; t < static_cast<int>(mesh.tris.size()); ++t)
        for (int k = 0; k < 3; ++k) {
            int a = mesh.tris[t][k], b = mesh.tris[t][(k + 1) % 3];
            sides.push_back({std::min(a, b), std::max(a, b), t, k});
        }
    std::sort(sides.begin(), sides.end(), [](const Side& a, const Side& b) {
        if (a.low != b.low) return a.low < b.low;
        if (a.high != b.high) return a.high < b.high;
        if (a.tri != b.tri) return a.tri < b.tri;
        return a.corner < b.corner;
    });
    mesh.across.assign(mesh.tris.size(), {{-1, -1, -1}});
    for (size_t i = 0; i < sides.size();) {
        size_t j = i;
        while (j < sides.size() && sides[j].low == sides[i].low && sides[j].high == sides[i].high) ++j;
        ++mesh.edge_count;
        if (j - i == 1) {
            ++mesh.border_edges;
        } else if (j - i > 2) {
            ++mesh.nonmanifold_edges;
        } else {
            const Side& s = sides[i];
            const Side& o = sides[i + 1];
            mesh.across[s.tri][s.corner] = o.tri;
            mesh.across[o.tri][o.corner] = s.tri;
            if (mesh.tris[s.tri][s.corner] == mesh.tris[o.tri][o.corner]) ++mesh.flipped_pairs;
        }
        i = j;
    }
    std::vector<int> parent(mesh.points.size());
    for (size_t i = 0; i < parent.size(); ++i) parent[i] = static_cast<int>(i);
    auto find = [&](int x) {
        while (parent[x] != x) x = parent[x] = parent[parent[x]];
        return x;
    };
    std::vector<char> used(mesh.points.size(), 0);
    for (const auto& tri : mesh.tris)
        for (int k = 0; k < 3; ++k) {
            used[tri[k]] = 1;
            parent[find(tri[k])] = find(tri[(k + 1) % 3]);
        }
    for (size_t i = 0; i < parent.size(); ++i)
        if (used[i] && find(static_cast<int>(i)) == static_cast<int>(i)) ++mesh.pieces;

    size_t n = mesh.tris.size();
    mesh.normal.resize(n);
    mesh.tangent.resize(n);
    mesh.bitangent.resize(n);
    mesh.area.resize(n);
    for (size_t t = 0; t < n; ++t) {
        const Vec3& a = mesh.points[mesh.tris[t][0]];
        Vec3 u = mesh.points[mesh.tris[t][1]] - a, v = mesh.points[mesh.tris[t][2]] - a;
        Vec3 c = u.cross(v);
        double twice = c.norm();
        mesh.area[t] = 0.5 * twice;
        mesh.normal[t] = twice > 0 ? c * (1.0 / twice) : Vec3(0, 0, 1);
        double length = u.norm();
        mesh.tangent[t] = length > 0 ? u * (1.0 / length) : Vec3(1, 0, 0);
        mesh.bitangent[t] = mesh.normal[t].cross(mesh.tangent[t]);
    }
}

// ---------------------------------------------------------------- S2: preparar la malla de entrada
//
// Las líneas de S4 van por aristas: necesitan triángulos regulares y unas tres aristas por cada arista final. Se divide
// por el lado más largo (Rivara: no empeora los ángulos y arregla los triángulos alargados) hasta que ninguna arista pase
// del tope, y después se voltean aristas hacia Delaunay donde la superficie es casi plana. La forma no cambia: los puntos
// nuevos están sobre las aristas de la malla original y solo se voltea entre caras casi coplanares.

struct RefineReport {
    int passes = 0, splits = 0, flips = 0;
};

static uint64_t edge_key(int a, int b) {
    return (static_cast<uint64_t>(std::min(a, b)) << 32) | static_cast<uint32_t>(std::max(a, b));
}

static RefineReport refine_mesh(Mesh& mesh, double max_edge) {
    RefineReport report;
    auto length2 = [&](int a, int b) {
        Vec3 d = mesh.points[a] - mesh.points[b];
        return d.dot(d);
    };
    const double limit2 = max_edge * max_edge;
    for (int pass = 0; pass < 64; ++pass) {
        // arista más larga de cada triángulo (empates por índice, para que sea determinista)
        const size_t count = mesh.tris.size();
        std::vector<int> longest(count);
        std::unordered_map<uint64_t, int> votes;  // triángulos que tienen esa arista como la más larga
        std::unordered_map<uint64_t, int> sides;  // triángulos que tienen esa arista
        votes.reserve(count * 2);
        sides.reserve(count * 3);
        bool any = false;
        for (size_t f = 0; f < count; ++f) {
            const auto& tri = mesh.tris[f];
            int best = 0;
            double best_length = -1;
            uint64_t best_key = 0;
            for (int k = 0; k < 3; ++k) {
                double l = length2(tri[k], tri[(k + 1) % 3]);
                uint64_t key = edge_key(tri[k], tri[(k + 1) % 3]);
                ++sides[key];
                if (l > best_length || (l == best_length && key < best_key)) { best_length = l; best = k; best_key = key; }
            }
            longest[f] = best;
            if (best_length > limit2) { ++votes[best_key]; any = true; }
        }
        if (!any) break;
        ++report.passes;
        // se parte una arista si es la más larga de todos sus triángulos: así cada triángulo se parte como mucho por un lado
        std::unordered_map<uint64_t, int> middle;
        for (const auto& item : votes)
            if (item.second == sides[item.first]) {
                int a = static_cast<int>(item.first >> 32), b = static_cast<int>(item.first & 0xffffffffu);
                middle[item.first] = static_cast<int>(mesh.points.size());
                mesh.points.push_back((mesh.points[a] + mesh.points[b]) * 0.5);
                ++report.splits;
            }
        if (middle.empty()) break;
        std::vector<std::array<int, 3>> next;
        next.reserve(count + middle.size() * 2);
        for (size_t f = 0; f < count; ++f) {
            const auto& tri = mesh.tris[f];
            int k = longest[f];
            int a = tri[k], b = tri[(k + 1) % 3], c = tri[(k + 2) % 3];
            auto found = middle.find(edge_key(a, b));
            if (found == middle.end()) { next.push_back(tri); continue; }
            int m = found->second;
            next.push_back({{a, m, c}});
            next.push_back({{m, b, c}});
        }
        mesh.tris.swap(next);
    }
    // volteos hacia Delaunay entre caras casi coplanares
    for (int pass = 0; pass < 4; ++pass) {
        std::unordered_map<uint64_t, std::vector<std::pair<int, int>>> owners;  // arista -> (triángulo, esquina)
        owners.reserve(mesh.tris.size() * 3);
        for (int f = 0; f < static_cast<int>(mesh.tris.size()); ++f)
            for (int k = 0; k < 3; ++k) owners[edge_key(mesh.tris[f][k], mesh.tris[f][(k + 1) % 3])].push_back({f, k});
        std::vector<char> touched(mesh.tris.size(), 0);
        int flipped = 0;
        auto normal_of = [&](int a, int b, int c) { return (mesh.points[b] - mesh.points[a]).cross(mesh.points[c] - mesh.points[a]); };
        auto angle_at = [&](int apex, int a, int b) {
            Vec3 u = mesh.points[a] - mesh.points[apex], w = mesh.points[b] - mesh.points[apex];
            return std::atan2(u.cross(w).norm(), u.dot(w));
        };
        std::vector<uint64_t> keys;
        keys.reserve(owners.size());
        for (const auto& item : owners) keys.push_back(item.first);
        std::sort(keys.begin(), keys.end());
        for (uint64_t key : keys) {
            const auto& list = owners[key];
            if (list.size() != 2) continue;
            int f1 = list[0].first, k1 = list[0].second, f2 = list[1].first, k2 = list[1].second;
            if (touched[f1] || touched[f2]) continue;
            int a = mesh.tris[f1][k1], b = mesh.tris[f1][(k1 + 1) % 3], c = mesh.tris[f1][(k1 + 2) % 3];
            if (mesh.tris[f2][k2] != b || mesh.tris[f2][(k2 + 1) % 3] != a) continue;  // orientación incoherente
            int d = mesh.tris[f2][(k2 + 2) % 3];
            if (c == d || owners.count(edge_key(c, d))) continue;
            Vec3 n1 = normal_of(a, b, c), n2 = normal_of(b, a, d);
            double l1 = n1.norm(), l2 = n2.norm();
            if (l1 <= 0 || l2 <= 0 || n1.dot(n2) < 0.995 * l1 * l2) continue;  // casi coplanares (unos 6°)
            if (angle_at(c, a, b) + angle_at(d, b, a) <= PI + 1e-9) continue;     // ya es Delaunay
            Vec3 m1 = normal_of(c, d, b), m2 = normal_of(d, c, a);
            if (m1.dot(n1) <= 0 || m2.dot(n1) <= 0) continue;                    // el cuadrilátero no es convexo
            mesh.tris[f1] = {{c, d, b}};
            mesh.tris[f2] = {{d, c, a}};
            touched[f1] = touched[f2] = 1;
            ++flipped;
        }
        report.flips += flipped;
        if (!flipped) break;
    }
    return report;
}

// ángulo de un vector en el marco de un triángulo
static double frame_angle(const Mesh& mesh, int t, const Vec3& v) {
    return std::atan2(v.dot(mesh.bitangent[t]), v.dot(mesh.tangent[t]));
}

// ---------------------------------------------------------------- S1: análisis

struct Analysis {
    double diagonal = 0, mean_edge = 0, total_area = 0;
    int features = 0;
    std::vector<char> feature_side;  // por (triángulo * 3 + esquina): esa arista es canto o borde
    std::array<double, 3> mirror = {{0, 0, 0}};
};

// fracción de vértices con pareja al reflejar en cada eje, por el centro de la caja
static double mirror_match(const Mesh& mesh, const Vec3& center, int axis, double tolerance) {
    const double step = std::max(tolerance, 1e-12);
    auto key = [&](const Vec3& p) {
        int64_t i = static_cast<int64_t>(std::floor(p.x / step)), j = static_cast<int64_t>(std::floor(p.y / step)),
                k = static_cast<int64_t>(std::floor(p.z / step));
        return (i * 73856093) ^ (j * 19349663) ^ (k * 83492791);
    };
    std::unordered_map<int64_t, std::vector<int>> cells;
    cells.reserve(mesh.points.size() * 2);
    for (size_t i = 0; i < mesh.points.size(); ++i) cells[key(mesh.points[i])].push_back(static_cast<int>(i));
    size_t matched = 0;
    for (const Vec3& p : mesh.points) {
        Vec3 q = p;
        if (axis == 0) q.x = 2 * center.x - p.x;
        else if (axis == 1) q.y = 2 * center.y - p.y;
        else q.z = 2 * center.z - p.z;
        bool found = false;
        for (int dx = -1; dx <= 1 && !found; ++dx)
            for (int dy = -1; dy <= 1 && !found; ++dy)
                for (int dz = -1; dz <= 1 && !found; ++dz) {
                    auto it = cells.find(key(Vec3(q.x + dx * step, q.y + dy * step, q.z + dz * step)));
                    if (it == cells.end()) continue;
                    for (int other : it->second)
                        if ((mesh.points[other] - q).norm() <= tolerance) { found = true; break; }
                }
        if (found) ++matched;
    }
    return mesh.points.empty() ? 0.0 : static_cast<double>(matched) / mesh.points.size();
}

// longitud mínima de una cadena de canto, como fracción de la diagonal del modelo
static const double FEATURE_MIN_CHAIN = 0.05;

static Analysis analyze(const Mesh& mesh, double feature_degrees) {
    Analysis result;
    Vec3 low = mesh.points[0], high = mesh.points[0];
    for (const Vec3& p : mesh.points) {
        low = Vec3(std::min(low.x, p.x), std::min(low.y, p.y), std::min(low.z, p.z));
        high = Vec3(std::max(high.x, p.x), std::max(high.y, p.y), std::max(high.z, p.z));
    }
    result.diagonal = (high - low).norm();
    result.feature_side.assign(mesh.tris.size() * 3, 0);
    const double cos_limit = std::cos(feature_degrees * PI / 180.0);
    double edge_sum = 0;
    size_t edge_total = 0;
    for (size_t t = 0; t < mesh.tris.size(); ++t) {
        result.total_area += mesh.area[t];
        for (int k = 0; k < 3; ++k) {
            edge_sum += (mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]]).norm();
            ++edge_total;
            int other = mesh.across[t][k];
            bool sharp = other < 0 || mesh.normal[t].dot(mesh.normal[other]) < cos_limit;
            if (sharp) {
                result.feature_side[t * 3 + k] = 1;
                if (other < 0 || static_cast<int>(t) < other) ++result.features;
            }
        }
    }
    result.mean_edge = edge_sum / std::max<size_t>(edge_total, 1);
    // un pliegue de verdad es largo: las cadenas cortas de aristas marcadas son ruido o detalle fino de una malla orgánica.
    // Los bordes abiertos siempre cuentan.
    std::vector<int> parent(mesh.points.size());
    for (size_t i = 0; i < parent.size(); ++i) parent[i] = static_cast<int>(i);
    auto find = [&](int x) {
        while (parent[x] != x) x = parent[x] = parent[parent[x]];
        return x;
    };
    auto is_crease = [&](size_t t, int k) { return result.feature_side[t * 3 + k] && mesh.across[t][k] >= 0; };
    for (size_t t = 0; t < mesh.tris.size(); ++t)
        for (int k = 0; k < 3; ++k)
            if (is_crease(t, k)) parent[find(mesh.tris[t][k])] = find(mesh.tris[t][(k + 1) % 3]);
    std::vector<double> chain(mesh.points.size(), 0.0);
    for (size_t t = 0; t < mesh.tris.size(); ++t)
        for (int k = 0; k < 3; ++k)
            if (is_crease(t, k) && static_cast<int>(t) < mesh.across[t][k])
                chain[find(mesh.tris[t][k])] += (mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]]).norm();
    for (size_t t = 0; t < mesh.tris.size(); ++t)
        for (int k = 0; k < 3; ++k)
            if (is_crease(t, k) && chain[find(mesh.tris[t][k])] < FEATURE_MIN_CHAIN * result.diagonal) {
                result.feature_side[t * 3 + k] = 0;
                if (static_cast<int>(t) < mesh.across[t][k]) --result.features;
            }
    Vec3 center = (low + high) * 0.5;
    // tolerancia: una fracción de la arista media, para no llamar simétrica a una malla densa cualquiera
    for (int axis = 0; axis < 3; ++axis) result.mirror[axis] = mirror_match(mesh, center, axis, 0.25 * result.mean_edge);
    return result;
}

// ---------------------------------------------------------------- S3: campo de direcciones (cruces, simetría 4)
//
// Cada cruz se guarda como z = e^(i 4 θ): así las cuatro direcciones de la cruz son el mismo número y suavizar es promediar.
// ρ(a, b): lo que hay que sumar a un ángulo en el marco de a para tenerlo en el de b. En la red se guarda c = e^(i 4 ρ).

struct Level {
    int count = 0;
    std::vector<int> first, target;
    std::vector<Complex> conn;
    std::vector<double> weight;
    std::vector<Complex> constraint;
    std::vector<Complex> align;   // tirón suave hacia las direcciones de curvatura (solo donde la superficie es anisótropa)
    std::vector<double> node_area;
    bool use_align = true;        // en niveles más finos que la cara pedida, el detalle no debe crear polos
    std::vector<int> parent;      // nodo del nivel siguiente
    std::vector<Complex> to_parent;  // e^(i 4 δ): del marco de este nodo al de su padre
};

struct LooseEdge {
    int from, to;
    Complex conn;
    double weight;
};

static void build_edges(Level& level, std::vector<LooseEdge>& edges) {
    std::sort(edges.begin(), edges.end(), [](const LooseEdge& a, const LooseEdge& b) {
        return a.from != b.from ? a.from < b.from : a.to < b.to;
    });
    level.first.assign(level.count + 1, 0);
    level.target.clear();
    level.conn.clear();
    level.weight.clear();
    for (size_t i = 0; i < edges.size();) {
        size_t j = i;
        Complex sum(0, 0);
        double weight = 0;
        while (j < edges.size() && edges[j].from == edges[i].from && edges[j].to == edges[i].to) {
            sum += edges[j].conn * edges[j].weight;
            weight += edges[j].weight;
            ++j;
        }
        double length = std::abs(sum);
        level.target.push_back(edges[i].to);
        level.conn.push_back(length > 0 ? sum / length : Complex(1, 0));
        level.weight.push_back(weight);
        level.first[edges[i].from + 1] = static_cast<int>(level.target.size());
        i = j;
    }
    for (int i = 0; i < level.count; ++i) level.first[i + 1] = std::max(level.first[i + 1], level.first[i]);
}

static Level finest_level(const Mesh& mesh, const Analysis& analysis) {
    Level level;
    level.count = static_cast<int>(mesh.tris.size());
    level.constraint.assign(level.count, Complex(0, 0));
    level.align.assign(level.count, Complex(0, 0));
    level.node_area = mesh.area;
    std::vector<LooseEdge> edges;
    edges.reserve(mesh.tris.size() * 3);
    for (int t = 0; t < level.count; ++t) {
        // tensor de curvatura de la cara (Cohen-Steiner y Morvan 2003): ángulo diedro x longitud por dirección doble
        Complex bending(0, 0);
        for (int k = 0; k < 3; ++k) {
            Vec3 edge = mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]];
            double length = edge.norm();
            if (length <= 0) continue;
            double here = frame_angle(mesh, t, edge);
            if (analysis.feature_side[t * 3 + k]) level.constraint[t] += std::polar(length, 4.0 * here);
            int other = mesh.across[t][k];
            if (other < 0 || analysis.feature_side[t * 3 + k]) continue;  // el campo no se propaga a través de un canto
            double rho = frame_angle(mesh, other, edge) - here;
            edges.push_back({t, other, std::polar(1.0, 4.0 * rho), length});
            double dihedral = std::atan2(mesh.normal[t].cross(mesh.normal[other]).dot(edge * (1.0 / length)),
                                         mesh.normal[t].dot(mesh.normal[other]));
            bending += std::polar(dihedral * length, 2.0 * here);
        }
        // al elevar al cuadrado, doble dirección -> cruz; el módulo sigue siendo la anisotropía (una esfera, casi cero)
        double strength = std::abs(bending);
        if (strength > 0) level.align[t] = bending * bending / strength;
    }
    build_edges(level, edges);
    return level;
}

static Level coarsen(Level& fine) {
    Level coarse;
    fine.parent.assign(fine.count, -1);
    fine.to_parent.assign(fine.count, Complex(1, 0));
    for (int i = 0; i < fine.count; ++i) {
        if (fine.parent[i] >= 0) continue;
        int best = -1, slot = -1;
        double best_weight = -1;
        for (int e = fine.first[i]; e < fine.first[i + 1]; ++e)
            if (fine.parent[fine.target[e]] < 0 && fine.target[e] != i && fine.weight[e] > best_weight) {
                best_weight = fine.weight[e];
                best = fine.target[e];
                slot = e;
            }
        int id = coarse.count++;
        fine.parent[i] = id;
        if (best >= 0) {
            fine.parent[best] = id;
            // el marco del padre es el de i: de `best` a i es la inversa de la conexión i -> best
            fine.to_parent[best] = std::conj(fine.conn[slot]);
        }
    }
    coarse.constraint.assign(coarse.count, Complex(0, 0));
    coarse.align.assign(coarse.count, Complex(0, 0));
    coarse.node_area.assign(coarse.count, 0.0);
    std::vector<LooseEdge> edges;
    edges.reserve(fine.target.size());
    for (int i = 0; i < fine.count; ++i) {
        coarse.constraint[fine.parent[i]] += fine.constraint[i] * fine.to_parent[i];
        coarse.align[fine.parent[i]] += fine.align[i] * fine.to_parent[i];
        coarse.node_area[fine.parent[i]] += fine.node_area[i];
        for (int e = fine.first[i]; e < fine.first[i + 1]; ++e) {
            int j = fine.target[e];
            if (fine.parent[i] == fine.parent[j]) continue;
            edges.push_back({fine.parent[i], fine.parent[j], fine.conn[e] * fine.to_parent[j] * std::conj(fine.to_parent[i]), fine.weight[e]});
        }
    }
    build_edges(coarse, edges);
    return coarse;
}

static void smooth(const Level& level, std::vector<Complex>& z, int rounds) {
    for (int round = 0; round < rounds; ++round) {
        const bool forward = round % 2 == 0;
        for (int step = 0; step < level.count; ++step) {
            int i = forward ? step : level.count - 1 - step;
            Complex sum(0, 0);
            if (std::abs(level.constraint[i]) > 0) {
                sum = level.constraint[i];  // los cantos mandan
            } else {
                for (int e = level.first[i]; e < level.first[i + 1]; ++e)
                    // la red guarda i -> j; para traer j a i se usa la inversa
                    sum += z[level.target[e]] * std::conj(level.conn[e]) * level.weight[e];
                if (level.use_align) sum += level.align[i];
            }
            double length = std::abs(sum);
            if (length > 1e-14) z[i] = sum / length;
        }
    }
}

static std::vector<Complex> solve_field(const Mesh& mesh, const Analysis& analysis, int target_faces, int& levels_used) {
    std::vector<Level> levels;
    levels.push_back(finest_level(mesh, analysis));
    while (levels.back().count > 64 && levels.size() < 24) {
        Level next = coarsen(levels.back());
        if (next.count >= levels.back().count) break;
        levels.push_back(std::move(next));
    }
    levels_used = static_cast<int>(levels.size());
    if (target_faces > 0) {
        const double face_area = analysis.total_area / target_faces;
        for (Level& level : levels) {
            double sum = 0;
            for (double a : level.node_area) sum += a;
            // en todos los niveles: sin este tirón, en un cilindro o un toro el campo gira libremente (cualquier ángulo es
            // igual de suave) y los lazos no se cierran. En los niveles finos es débil y no crea polos por el detalle.
            level.use_align = getenv("MBL_ALIGN_COARSE") ? sum / std::max(level.count, 1) >= 0.5 * face_area : true;
        }
    }
    std::vector<Complex> z(levels.back().count, Complex(1, 0));
    smooth(levels.back(), z, 200);
    for (int l = static_cast<int>(levels.size()) - 2; l >= 0; --l) {
        const Level& fine = levels[l];
        std::vector<Complex> finer(fine.count);
        for (int i = 0; i < fine.count; ++i) finer[i] = z[fine.parent[i]] * std::conj(fine.to_parent[i]);
        smooth(fine, finer, 30);
        z.swap(finer);
    }
    return z;
}

struct FieldReport {
    int singular[9] = {0};  // índice en cuartos: -4..+4
    int singular_total = 0, vertices_checked = 0, vertices_skipped = 0;
    double index_sum = 0, mean_turn_degrees = 0, feature_error_degrees = 0;
    uint64_t hash = 14695981039346656037ULL;
    std::vector<std::pair<int, int>> poles;  // (vértice, índice en cuartos)
};

// salto de la cruz al pasar de a a b, en (-π/4, π/4]
static double jump(const Mesh& mesh, const std::vector<Complex>& z, int a, int b, const Vec3& edge) {
    double rho = frame_angle(mesh, b, edge) - frame_angle(mesh, a, edge);
    return std::arg(z[b] * std::conj(z[a] * std::polar(1.0, 4.0 * rho))) / 4.0;
}

static FieldReport inspect_field(const Mesh& mesh, const Analysis& analysis, const std::vector<Complex>& z) {
    FieldReport report;
    // esquinas de cada vértice
    std::vector<std::vector<std::pair<int, int>>> corners(mesh.points.size());
    for (int t = 0; t < static_cast<int>(mesh.tris.size()); ++t)
        for (int k = 0; k < 3; ++k) corners[mesh.tris[t][k]].push_back({t, k});
    for (size_t v = 0; v < mesh.points.size(); ++v) {
        if (corners[v].empty()) continue;
        // recorrido en sentido antihorario alrededor de v
        int start = corners[v][0].first, corner = corners[v][0].second, t = start, k = corner;
        double turning = 0, angles = 0;
        size_t walked = 0;
        bool closed = false, valid = true;
        while (walked <= corners[v].size()) {
            const Vec3& p = mesh.points[v];
            Vec3 ab = mesh.points[mesh.tris[t][(k + 1) % 3]] - p, ac = mesh.points[mesh.tris[t][(k + 2) % 3]] - p;
            angles += std::atan2(ab.cross(ac).norm(), ab.dot(ac));
            // la arista (v[k+2], v[k]) lleva al triángulo siguiente
            int side = (k + 2) % 3, next = mesh.across[t][side];
            if (next < 0 || analysis.feature_side[t * 3 + side] < 0) { valid = false; break; }
            int next_k = -1;
            for (int c = 0; c < 3; ++c) if (mesh.tris[next][c] == static_cast<int>(v)) next_k = c;
            if (next_k < 0 || mesh.tris[next][(next_k + 1) % 3] != mesh.tris[t][(k + 2) % 3]) { valid = false; break; }
            turning += jump(mesh, z, t, next, ac);
            t = next;
            k = next_k;
            ++walked;
            if (t == start) { closed = true; break; }
        }
        if (!valid || !closed || walked != corners[v].size()) { ++report.vertices_skipped; continue; }
        ++report.vertices_checked;
        double index = (turning + (2.0 * PI - angles)) / (2.0 * PI);
        int quarters = static_cast<int>(std::lround(index * 4.0));
        report.index_sum += quarters / 4.0;
        if (quarters != 0) {
            report.poles.push_back({static_cast<int>(v), quarters});
            ++report.singular_total;
            report.singular[std::min(8, std::max(0, quarters + 4))]++;
        }
    }
    double turn_sum = 0, turn_weight = 0, error_sum = 0, error_weight = 0;
    for (int t = 0; t < static_cast<int>(mesh.tris.size()); ++t)
        for (int k = 0; k < 3; ++k) {
            Vec3 edge = mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]];
            double length = edge.norm();
            if (length <= 0) continue;
            if (analysis.feature_side[t * 3 + k]) {
                double off = std::arg(z[t] * std::polar(1.0, -4.0 * frame_angle(mesh, t, edge))) / 4.0;
                error_sum += std::abs(off) * length;
                error_weight += length;
            } else if (mesh.across[t][k] >= 0) {
                turn_sum += std::abs(jump(mesh, z, t, mesh.across[t][k], edge)) * length;
                turn_weight += length;
            }
        }
    report.mean_turn_degrees = turn_weight > 0 ? turn_sum / turn_weight * 180.0 / PI : 0.0;
    report.feature_error_degrees = error_weight > 0 ? error_sum / error_weight * 180.0 / PI : 0.0;
    for (const Complex& value : z) {
        // redondeo para que la huella no dependa del último bit
        int64_t parts[2] = {std::llround(value.real() * 1e6), std::llround(value.imag() * 1e6)};
        for (int64_t part : parts) {
            report.hash ^= static_cast<uint64_t>(part);
            report.hash *= 1099511628211ULL;
        }
    }
    return report;
}

// dirección 3D de cada cruz, para quien la use después (float x, y, z por triángulo)
static bool write_field(const char* path, const Mesh& mesh, const std::vector<Complex>& z) {
    FILE* file = fopen(path, "wb");
    if (!file) return false;
    for (size_t t = 0; t < z.size(); ++t) {
        double theta = std::arg(z[t]) / 4.0;
        Vec3 d = mesh.tangent[t] * std::cos(theta) + mesh.bitangent[t] * std::sin(theta);
        float values[3] = {static_cast<float>(d.x), static_cast<float>(d.y), static_cast<float>(d.z)};
        fwrite(values, sizeof(float), 3, file);
    }
    fclose(file);
    return true;
}

// ---------------------------------------------------------------- S4: trazado y parches
//
// Desde cada polo del campo se siguen sus separatrices (las direcciones de la cruz que salen del polo) hasta otro polo, un
// borde, un canto u otra línea (grafo de motocicletas: todas avanzan a la vez y la que choca se para, dejando una unión en T).
// Las líneas van por aristas de la malla de entrada; para que no se desvíen, cada paso corrige el error lateral acumulado
// respecto a la línea de campo ideal. Después, los parches que no son un disco de cuatro esquinas se parten con líneas nuevas.

struct EdgeGraph {
    std::vector<std::array<int, 2>> ends;
    std::vector<std::array<int, 2>> faces;              // -1 si no hay segunda cara
    std::vector<std::vector<std::pair<int, int>>> around;  // por vértice: (vecino, arista)
    std::vector<int> side_edge;                         // por (triángulo * 3 + esquina): arista
};

static EdgeGraph edge_graph(const Mesh& mesh) {
    EdgeGraph graph;
    graph.around.resize(mesh.points.size());
    graph.side_edge.assign(mesh.tris.size() * 3, -1);
    std::unordered_map<uint64_t, int> index;
    index.reserve(mesh.tris.size() * 3);
    for (int t = 0; t < static_cast<int>(mesh.tris.size()); ++t)
        for (int k = 0; k < 3; ++k) {
            int a = mesh.tris[t][k], b = mesh.tris[t][(k + 1) % 3];
            uint64_t key = (static_cast<uint64_t>(std::min(a, b)) << 32) | static_cast<uint32_t>(std::max(a, b));
            auto found = index.find(key);
            int e;
            if (found == index.end()) {
                e = static_cast<int>(graph.ends.size());
                index[key] = e;
                graph.ends.push_back({{std::min(a, b), std::max(a, b)}});
                graph.faces.push_back({{t, -1}});
                graph.around[a].push_back({b, e});
                graph.around[b].push_back({a, e});
            } else {
                e = found->second;
                graph.faces[e][1] = t;
            }
            graph.side_edge[t * 3 + k] = e;
        }
    return graph;
}

// cruz de cada vértice: la media de las de sus caras, en un marco propio del vértice
struct VertexFrames {
    std::vector<Vec3> normal, tangent, bitangent;
    std::vector<double> phase;  // ángulo de una de las cuatro direcciones, en el marco del vértice
};

static VertexFrames vertex_frames(const Mesh& mesh, const EdgeGraph& graph, const std::vector<Complex>& z) {
    size_t n = mesh.points.size();
    VertexFrames frames;
    frames.normal.assign(n, Vec3());
    frames.tangent.assign(n, Vec3(1, 0, 0));
    frames.bitangent.assign(n, Vec3(0, 1, 0));
    frames.phase.assign(n, 0.0);
    for (size_t t = 0; t < mesh.tris.size(); ++t)
        for (int v : mesh.tris[t]) frames.normal[v] = frames.normal[v] + mesh.normal[t] * mesh.area[t];
    for (size_t v = 0; v < n; ++v) {
        double length = frames.normal[v].norm();
        frames.normal[v] = length > 0 ? frames.normal[v] * (1.0 / length) : Vec3(0, 0, 1);
        if (graph.around[v].empty()) continue;
        Vec3 out = mesh.points[graph.around[v][0].first] - mesh.points[v];
        Vec3 flat = out - frames.normal[v] * out.dot(frames.normal[v]);
        double size = flat.norm();
        frames.tangent[v] = size > 0 ? flat * (1.0 / size) : Vec3(1, 0, 0);
        frames.bitangent[v] = frames.normal[v].cross(frames.tangent[v]);
    }
    std::vector<Complex> sum(n, Complex(0, 0));
    for (size_t t = 0; t < mesh.tris.size(); ++t) {
        double theta = std::arg(z[t]) / 4.0;
        Vec3 d = mesh.tangent[t] * std::cos(theta) + mesh.bitangent[t] * std::sin(theta);
        for (int v : mesh.tris[t]) {
            double phi = std::atan2(d.dot(frames.bitangent[v]), d.dot(frames.tangent[v]));
            sum[v] += std::polar(mesh.area[t], 4.0 * phi);
        }
    }
    for (size_t v = 0; v < n; ++v) frames.phase[v] = std::arg(sum[v]) / 4.0;
    return frames;
}

// ángulo en (-π/4, π/4] equivalente bajo giros de 90°
static double wrap_quarter(double a) { return std::arg(std::polar(1.0, 4.0 * a)) / 4.0; }

static double frame_phi(const VertexFrames& frames, int v, const Vec3& d) {
    return std::atan2(d.dot(frames.bitangent[v]), d.dot(frames.tangent[v]));
}

// dirección de la cruz del vértice más parecida a `d`
static Vec3 cross_direction(const VertexFrames& frames, int v, const Vec3& d) {
    double phi = frame_phi(frames, v, d);
    double best = phi + wrap_quarter(frames.phase[v] - phi);
    return frames.tangent[v] * std::cos(best) + frames.bitangent[v] * std::sin(best);
}

static Vec3 unit(const Vec3& v) {
    double n = v.norm();
    return n > 0 ? v * (1.0 / n) : Vec3();
}

static Vec3 flatten(const Vec3& d, const Vec3& normal) { return d - normal * d.dot(normal); }

// ángulo con signo de u a w alrededor de la normal
static double signed_angle(const Vec3& u, const Vec3& w, const Vec3& normal) {
    return std::atan2(normal.dot(u.cross(w)), u.dot(w));
}

struct Tracer {
    const Mesh* mesh = nullptr;
    EdgeGraph graph;
    VertexFrames frames;
    std::vector<char> traced, stop, on_line, border;
    std::vector<int> degree;  // aristas trazadas por vértice
    std::vector<int> head;    // línea viva cuya cabeza está en el vértice
    std::vector<char> fixed_edge;  // cantos y bordes: no se borran nunca
    std::map<int, int> pole_quarters;
    int lines = 0;
};

static int edge_between(const Tracer& tracer, int a, int b) {
    for (const auto& step : tracer.graph.around[a])
        if (step.first == b) return step.second;
    return -1;
}

static void mark_edge(Tracer& tracer, int e) {
    if (tracer.traced[e]) return;
    tracer.traced[e] = 1;
    ++tracer.degree[tracer.graph.ends[e][0]];
    ++tracer.degree[tracer.graph.ends[e][1]];
}

struct Walker {
    int from = -1, at = -1;
    Vec3 heading;
    double lateral = 0;
    bool alive = true;
    int origin = -1, steps = 0;
};

// un paso de una línea: la arista que avanza y mantiene la línea más cerca de la línea de campo ideal
static void walk_step(Tracer& tracer, std::vector<Walker>& walkers, int index) {
    const Mesh& mesh = *tracer.mesh;
    Walker& walker = walkers[index];
    int at = walker.at;
    const Vec3& normal = tracer.frames.normal[at];
    // en el polo la cruz no vale: se usa la dirección de salida tal cual
    Vec3 h = walker.from < 0 && tracer.pole_quarters.count(at) ? unit(flatten(walker.heading, normal))
                                                              : unit(cross_direction(tracer.frames, at, walker.heading));
    Vec3 side = normal.cross(h);
    auto finish = [&](int next, int next_edge, double lateral) {
        mark_edge(tracer, next_edge);
        if (tracer.head[at] == index) tracer.head[at] = -1;
        walker.lateral = lateral;
        walker.heading = h;
        walker.from = at;
        walker.at = next;
        ++walker.steps;
        if (tracer.stop[next] || tracer.on_line[next]) {
            walker.alive = false;
            return;
        }
        tracer.on_line[next] = 1;
        tracer.head[next] = index;
    };
    // si justo delante hay un polo o la cabeza de otra línea que viene de frente, unirse a ella
    int snap = -1, snap_edge = -1, snap_other = -1;
    double snap_best = 0.5;
    for (const auto& step : tracer.graph.around[at]) {
        if (step.first == walker.from || tracer.traced[step.second]) continue;
        int w = step.first, other = tracer.head[w];
        bool pole = tracer.pole_quarters.count(w) != 0;
        bool facing = other >= 0 && other != index && walkers[other].alive && walkers[other].heading.dot(h) < -0.5;
        bool home = w == walker.origin && walker.steps >= 3;  // un lazo que vuelve a su salida se cierra en ella
        if (!pole && !facing && !home) continue;
        Vec3 d = mesh.points[w] - mesh.points[at];
        double length = d.norm();
        if (length <= 0) continue;
        double c = d.dot(h) / length;
        if (c > snap_best) { snap_best = c; snap = w; snap_edge = step.second; snap_other = facing ? other : -1; }
    }
    if (snap >= 0) {
        finish(snap, snap_edge, 0.0);
        walker.alive = false;
        if (snap_other >= 0) {
            walkers[snap_other].alive = false;
            tracer.head[snap] = -1;
        }
        return;
    }
    for (double minimum : {0.3, 0.0}) {
        int next = -1, next_edge = -1;
        double best = 1e300, best_lateral = 0;
        for (const auto& step : tracer.graph.around[at]) {
            if (step.first == walker.from || tracer.traced[step.second]) continue;
            Vec3 d = mesh.points[step.first] - mesh.points[at];
            double length = d.norm(), along = d.dot(h);
            if (length <= 0 || along <= minimum * length) continue;
            double lateral = walker.lateral + d.dot(side);
            double score = std::abs(lateral) - 0.25 * along;
            if (score < best) {
                best = score;
                best_lateral = lateral;
                next = step.first;
                next_edge = step.second;
            }
        }
        if (next >= 0) {
            finish(next, next_edge, best_lateral);
            return;
        }
    }
    walker.alive = false;
    if (tracer.head[at] == index) tracer.head[at] = -1;
}

// avanza todas las líneas a la vez hasta que se paran
static void run_walkers(Tracer& tracer, std::vector<Walker>& walkers) {
    const size_t limit = 4 * tracer.mesh->points.size();
    for (size_t i = 0; i < walkers.size(); ++i)
        if (walkers[i].alive) tracer.head[walkers[i].at] = static_cast<int>(i);
    for (size_t round = 0; round < limit; ++round) {
        bool any = false;
        for (size_t i = 0; i < walkers.size(); ++i)
            if (walkers[i].alive) {
                walk_step(tracer, walkers, static_cast<int>(i));
                any = true;
            }
        if (!any) break;
    }
    for (const Walker& walker : walkers)
        if (walker.at >= 0) tracer.head[walker.at] = -1;
}

static Walker start_walker(Tracer& tracer, int p, const Vec3& heading) {
    Walker walker;
    walker.at = p;
    walker.origin = p;
    walker.heading = heading;
    tracer.on_line[p] = 1;
    ++tracer.lines;
    return walker;
}

// separatrices de un polo de índice k/4: alrededor del polo, la cruz gira k/4 de lo que gira el radio,
// θ(α) = (k/4) α + c. Las salidas son los α en que la cruz apunta hacia fuera: α = 4 (c - m π/2) / (4 - k), m = 0..3-k.
// c se ajusta con los vecinos (media en e^(4ic): la cruz no distingue giros de 90°, y (k/4)·2π tampoco).
static std::vector<Vec3> separatrices(const Tracer& tracer, int p, int quarters) {
    const Mesh& mesh = *tracer.mesh;
    const VertexFrames& frames = tracer.frames;
    const Vec3& normal = frames.normal[p];
    Complex sum(0, 0);
    for (const auto& step : tracer.graph.around[p]) {
        int w = step.first;
        Vec3 radial = flatten(mesh.points[w] - mesh.points[p], normal);
        Vec3 cross = flatten(frames.tangent[w] * std::cos(frames.phase[w]) + frames.bitangent[w] * std::sin(frames.phase[w]), normal);
        if (radial.norm() <= 0 || cross.norm() <= 0) continue;
        double alpha = std::atan2(radial.dot(frames.bitangent[p]), radial.dot(frames.tangent[p]));
        double theta = std::atan2(cross.dot(frames.bitangent[p]), cross.dot(frames.tangent[p]));
        sum += std::polar(radial.norm(), 4.0 * (theta - quarters / 4.0 * alpha));
    }
    double c = std::arg(sum) / 4.0;
    int wanted = 4 - quarters;
    std::vector<Vec3> out;
    if (wanted <= 0) return out;
    for (int m = 0; m < wanted; ++m) {
        double alpha = 4.0 * (c - m * PI / 2) / wanted;
        out.push_back(frames.tangent[p] * std::cos(alpha) + frames.bitangent[p] * std::sin(alpha));
    }
    return out;
}

static void trace_from_poles(Tracer& tracer, const std::vector<int>& poles) {
    const Mesh& mesh = *tracer.mesh;
    std::vector<Walker> walkers;
    for (int p : poles) {
        // índice +1/4 (3 aristas) saca 3 líneas; -1/4, 5
        for (const Vec3& d : separatrices(tracer, p, tracer.pole_quarters[p])) {
            // ya hay una línea (un canto) en esa dirección
            bool taken = false;
            for (const auto& step : tracer.graph.around[p])
                if (tracer.traced[step.second] &&
                    unit(flatten(mesh.points[step.first] - mesh.points[p], tracer.frames.normal[p])).dot(unit(d)) > 0.9) taken = true;
            if (getenv("MBL_CORE_DEBUG"))
                fprintf(stderr, "polo v%d: salida (%.3f %.3f %.3f)%s\n", p, d.x, d.y, d.z, taken ? " ya trazada" : "");
            if (!taken) walkers.push_back(start_walker(tracer, p, d));
        }
    }
    run_walkers(tracer, walkers);
}

// ---------------------------------------------------------------- S4b: disposición (nodos, tramos, parches, esquinas)

struct Segment {
    int a = -1, b = -1;              // nodos de los extremos
    std::vector<int> verts, edges;   // edges[j] une verts[j] y verts[j + 1]
    double length = 0;
    int divisions = 1;
};

struct Occurrence {
    int vertex = -1, wedge = -1;
    double fan = 0, angle = 0;       // ángulo interior: el de los triángulos y el corregido con la forma de la línea
    std::vector<int> fan_targets;    // vecinos por aristas interiores dentro de la cuña
    bool node = false;
    int quarters = 2;                // ángulo interior en cuartos de vuelta: 1 esquina, 2 recto, 3 cóncavo
};

struct PatchLoop {
    std::vector<int> halves;         // (triángulo * 3 + esquina): de verts[k] a verts[k+1] de ese triángulo
    std::vector<Occurrence> occ;     // occ[i]: el vértice en que acaba halves[i]
};

struct Patch {
    std::vector<int> tris;
    std::vector<PatchLoop> loops;
    int wedges = 0, euler = 0, corners = 0;
    bool good = false;
    std::string reason;
    std::array<std::vector<std::pair<int, bool>>, 4> sides;  // (tramo, recorrido al revés)
    int first_corner = -1;           // ocurrencia de la esquina donde empieza el lado 0 (en loops[0])
    std::array<int, 4> side_start = {{-1, -1, -1, -1}};  // vértice de la malla donde empieza cada lado
    bool degenerate = false;         // tras el reparto, algún par de lados opuestos suma cero: el parche se colapsa
};

struct Layout {
    std::vector<char> node;
    std::vector<Segment> segments;
    std::vector<std::pair<int, int>> edge_segment;  // por arista trazada: (tramo, posición)
    std::vector<int> face_patch;
    std::vector<int> wedge;                         // raíz de cada esquina (triángulo * 3 + esquina)
    std::vector<Patch> patches;
    int good = 0;
    std::map<std::string, int> reasons;
};

static double corner_angle(const Mesh& mesh, int t, int k) {
    const Vec3& p = mesh.points[mesh.tris[t][k]];
    Vec3 a = mesh.points[mesh.tris[t][(k + 1) % 3]] - p, b = mesh.points[mesh.tris[t][(k + 2) % 3]] - p;
    return std::atan2(a.cross(b).norm(), a.dot(b));
}

static bool boundary_half(const Tracer& tracer, int t, int k) {
    return tracer.mesh->across[t][k] < 0 || tracer.traced[tracer.graph.side_edge[t * 3 + k]];
}

// punto a distancia `radius` siguiendo una cadena de aristas trazadas de grado 2, desde v por el vecino `first`
static Vec3 chain_point(const Tracer& tracer, const Layout& layout, int v, int first, double radius, std::vector<int>* passed) {
    const Mesh& mesh = *tracer.mesh;
    int prev = v, at = first;
    double walked = 0;
    for (size_t guard = 0; guard < mesh.points.size(); ++guard) {
        double step = (mesh.points[at] - mesh.points[prev]).norm();
        if (walked + step >= radius && step > 0) {
            double s = (radius - walked) / step;
            return mesh.points[prev] + (mesh.points[at] - mesh.points[prev]) * s;
        }
        walked += step;
        if (passed) passed->push_back(at);
        if (at == v || tracer.degree[at] != 2 || (!layout.node.empty() && layout.node[at])) break;
        int next = -1;
        for (const auto& s : tracer.graph.around[at])
            if (tracer.traced[s.second] && s.first != prev) { next = s.first; break; }
        if (next < 0) break;
        prev = at;
        at = next;
    }
    return mesh.points[at];
}

static int find_root(std::vector<int>& parent, int x) {
    while (parent[x] != x) x = parent[x] = parent[parent[x]];
    return x;
}

// lados de un parche: tramos entre esquinas, en el orden del contorno. Devuelve el motivo si no salen cuatro.
static std::string build_sides(const Tracer& tracer, const Layout& layout, Patch& patch) {
    const Mesh& mesh = *tracer.mesh;
    for (auto& list : patch.sides) list.clear();
    const PatchLoop& loop = patch.loops[0];
    const int count = static_cast<int>(loop.halves.size());
    int side = 0;
    patch.side_start[0] = loop.occ[patch.first_corner].vertex;
    for (int s = 1; s <= count; ++s) {
        int i = (patch.first_corner + s) % count;
        int half = loop.halves[i], t = half / 3, k = half % 3;
        int from = mesh.tris[t][k];
        int e = tracer.graph.side_edge[half];
        auto where = layout.edge_segment[e];
        if (where.first < 0) return "sin_tramo";
        const Segment& seg = layout.segments[where.first];
        bool reversed = seg.verts[where.second] != from;
        auto& list = patch.sides[side];
        if (list.empty() || loop.occ[(i - 1 + count) % count].node) list.push_back({where.first, reversed});
        if (loop.occ[i].node && s < count) {
            int turn = 2 - loop.occ[i].quarters;
            for (int step = 0; step < turn; ++step) {
                ++side;
                if (side > 3) return "lados";
                patch.side_start[side] = loop.occ[i].vertex;
            }
        }
    }
    // la esquina de salida, si es una punta, deja el lado 3 vacío
    if (loop.occ[patch.first_corner].quarters == 0 && side == 2) {
        side = 3;
        patch.side_start[3] = loop.occ[patch.first_corner].vertex;
    }
    return side == 3 ? "" : "lados";
}

// último recurso para un parche disco que no ha quedado bien tras partir y juntar: se trata como rectángulo con sus
// cuatro nodos más cerrados como esquinas. El mapa de Tutte sigue sin pliegues aunque la forma sea en L o tenga cinco
// esquinas; los quads quedan algo deformados ahí, pero no hay agujero.
static int force_four_corners(const Tracer& tracer, Layout& layout) {
    const Mesh& mesh = *tracer.mesh;
    int forced = 0;
    for (Patch& patch : layout.patches) {
        if (patch.good || patch.loops.size() != 1 || patch.euler != 1) continue;
        auto& occs = patch.loops[0].occ;
        const auto& halves = patch.loops[0].halves;
        const int count = static_cast<int>(occs.size());
        // longitud acumulada del contorno hasta cada ocurrencia
        std::vector<double> arc(count + 1, 0.0);
        for (int i = 0; i < count; ++i) {
            int t = halves[i] / 3, k = halves[i] % 3;
            arc[i + 1] = arc[i] + (mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]]).norm();
        }
        std::vector<int> nodes;
        for (int i = 0; i < count; ++i) if (occs[i].node) nodes.push_back(i);
        if (nodes.size() < 4) continue;
        // como mucho los 12 nodos más cerrados, y de ellos la combinación de cuatro que mejor iguala los lados opuestos
        std::sort(nodes.begin(), nodes.end(), [&](int x, int y) { return occs[x].angle < occs[y].angle; });
        if (nodes.size() > 12) nodes.resize(12);
        std::sort(nodes.begin(), nodes.end());
        const int m = static_cast<int>(nodes.size());
        double best = 1e300;
        std::array<int, 4> pick = {{0, 0, 0, 0}};
        for (int a = 0; a < m; ++a)
            for (int b = a + 1; b < m; ++b)
                for (int c = b + 1; c < m; ++c)
                    for (int d = c + 1; d < m; ++d) {
                        int idx[4] = {nodes[a], nodes[b], nodes[c], nodes[d]};
                        double len[4];
                        for (int s = 0; s < 4; ++s) {
                            int from = idx[s], to = idx[(s + 1) % 4];
                            len[s] = to > from ? arc[to + 1] - arc[from + 1] : arc[count] - arc[from + 1] + arc[to + 1];
                        }
                        double mismatch = std::pow((len[0] - len[2]) / (len[0] + len[2] + 1e-30), 2) +
                                          std::pow((len[1] - len[3]) / (len[1] + len[3] + 1e-30), 2);
                        double angles = 0;
                        for (int s = 0; s < 4; ++s) angles += std::pow((occs[idx[s]].angle - PI / 2) / (PI / 2), 2);
                        double score = 4.0 * mismatch + angles;
                        if (score < best) { best = score; pick = {{idx[0], idx[1], idx[2], idx[3]}}; }
                    }
        std::vector<int> saved;
        for (const Occurrence& occ : occs) saved.push_back(occ.quarters);
        for (Occurrence& occ : occs) if (occ.node) occ.quarters = 2;
        for (int c = 0; c < 4; ++c) occs[pick[c]].quarters = 1;
        patch.first_corner = pick[0];
        std::string reason = build_sides(tracer, layout, patch);
        if (!reason.empty()) {
            for (size_t i = 0; i < occs.size(); ++i) occs[i].quarters = saved[i];
            continue;
        }
        --layout.reasons[patch.reason];
        if (layout.reasons[patch.reason] == 0) layout.reasons.erase(patch.reason);
        patch.reason.clear();
        patch.corners = 4;
        patch.good = true;
        ++layout.good;
        ++forced;
    }
    return forced;
}

static Layout build_layout(const Tracer& tracer, double radius) {
    const Mesh& mesh = *tracer.mesh;
    const size_t n = mesh.points.size(), tri_count = mesh.tris.size();
    Layout layout;
    // nodos: donde no se juntan exactamente dos aristas trazadas, los polos, y donde una cadena gira de golpe
    layout.node.assign(n, 0);
    std::vector<double> turn(n, PI);
    std::vector<int> candidates;
    for (size_t v = 0; v < n; ++v)
        if (tracer.degree[v] > 0 && (tracer.degree[v] != 2 || tracer.pole_quarters.count(static_cast<int>(v)))) layout.node[v] = 1;
    for (size_t v = 0; v < n; ++v) {
        if (tracer.degree[v] != 2 || layout.node[v]) continue;
        int ends[2], found = 0;
        for (const auto& s : tracer.graph.around[v])
            if (tracer.traced[s.second] && found < 2) ends[found++] = s.first;
        Vec3 a = flatten(chain_point(tracer, layout, static_cast<int>(v), ends[0], radius, nullptr) - mesh.points[v], tracer.frames.normal[v]);
        Vec3 b = flatten(chain_point(tracer, layout, static_cast<int>(v), ends[1], radius, nullptr) - mesh.points[v], tracer.frames.normal[v]);
        turn[v] = std::atan2(a.cross(b).norm(), a.dot(b));
        if (turn[v] < 2.0 * PI / 3.0) candidates.push_back(static_cast<int>(v));
    }
    std::vector<char> is_candidate(n, 0);
    for (int v : candidates) is_candidate[v] = 1;
    for (int v : candidates) {
        // de varios vértices que giran juntos, solo el que más gira
        bool best = true;
        int ends[2], found = 0;
        for (const auto& s : tracer.graph.around[v])
            if (tracer.traced[s.second] && found < 2) ends[found++] = s.first;
        for (int side = 0; side < 2 && best; ++side) {
            std::vector<int> passed;
            chain_point(tracer, layout, v, ends[side], radius, &passed);
            for (int w : passed)
                if (w != v && is_candidate[w] && (turn[w] < turn[v] || (turn[w] == turn[v] && w < v))) best = false;
        }
        if (best) layout.node[v] = 1;
    }
    // tramos: cadenas de aristas trazadas entre nodos
    const size_t edge_count = tracer.graph.ends.size();
    layout.edge_segment.assign(edge_count, {-1, -1});
    auto walk_segment = [&](int start, int first_edge) {
        Segment seg;
        seg.a = start;
        seg.verts.push_back(start);
        int at = start, e = first_edge;
        for (size_t guard = 0; guard <= edge_count; ++guard) {
            int other = tracer.graph.ends[e][0] == at ? tracer.graph.ends[e][1] : tracer.graph.ends[e][0];
            layout.edge_segment[e] = {static_cast<int>(layout.segments.size()), static_cast<int>(seg.edges.size())};
            seg.edges.push_back(e);
            seg.verts.push_back(other);
            seg.length += (mesh.points[other] - mesh.points[at]).norm();
            at = other;
            if (layout.node[at]) break;
            int next = -1;
            for (const auto& s : tracer.graph.around[at])
                if (tracer.traced[s.second] && layout.edge_segment[s.second].first < 0) { next = s.second; break; }
            if (next < 0) break;
            e = next;
        }
        seg.b = at;
        layout.segments.push_back(seg);
    };
    for (size_t v = 0; v < n; ++v)
        if (layout.node[v])
            for (const auto& s : tracer.graph.around[v])
                if (tracer.traced[s.second] && layout.edge_segment[s.second].first < 0) walk_segment(static_cast<int>(v), s.second);
    // lazos cerrados sin ningún nodo: se les pone uno
    for (size_t e = 0; e < edge_count; ++e)
        if (tracer.traced[e] && layout.edge_segment[e].first < 0) {
            int v = tracer.graph.ends[e][0];
            layout.node[v] = 1;
            for (const auto& s : tracer.graph.around[v])
                if (tracer.traced[s.second] && layout.edge_segment[s.second].first < 0) walk_segment(v, s.second);
        }
    // parches: triángulos unidos por aristas no trazadas; cuñas: esquinas de un vértice unidas igual
    std::vector<int> face_parent(tri_count), corner_parent(tri_count * 3);
    for (size_t i = 0; i < tri_count; ++i) face_parent[i] = static_cast<int>(i);
    for (size_t i = 0; i < tri_count * 3; ++i) corner_parent[i] = static_cast<int>(i);
    for (int t = 0; t < static_cast<int>(tri_count); ++t)
        for (int k = 0; k < 3; ++k) {
            if (boundary_half(tracer, t, k)) continue;
            int o = mesh.across[t][k];
            int a = mesh.tris[t][k], b = mesh.tris[t][(k + 1) % 3];
            face_parent[find_root(face_parent, t)] = find_root(face_parent, o);
            for (int c = 0; c < 3; ++c) {
                if (mesh.tris[o][c] == a) corner_parent[find_root(corner_parent, t * 3 + k)] = find_root(corner_parent, o * 3 + c);
                if (mesh.tris[o][c] == b) corner_parent[find_root(corner_parent, t * 3 + (k + 1) % 3)] = find_root(corner_parent, o * 3 + c);
            }
        }
    layout.wedge.resize(tri_count * 3);
    for (size_t i = 0; i < tri_count * 3; ++i) layout.wedge[i] = find_root(corner_parent, static_cast<int>(i));
    std::unordered_map<int, int> label;
    layout.face_patch.resize(tri_count);
    for (size_t t = 0; t < tri_count; ++t) {
        int root = find_root(face_parent, static_cast<int>(t));
        auto found = label.find(root);
        if (found == label.end()) {
            found = label.insert({root, static_cast<int>(layout.patches.size())}).first;
            layout.patches.emplace_back();
        }
        layout.face_patch[t] = found->second;
        layout.patches[found->second].tris.push_back(static_cast<int>(t));
    }
    // contornos de cada parche, con el interior a la izquierda
    std::vector<char> seen(tri_count * 3, 0);
    for (Patch& patch : layout.patches) {
        int boundary = 0;
        std::set<int> wedges;
        for (int t : patch.tris)
            for (int k = 0; k < 3; ++k) {
                wedges.insert(layout.wedge[t * 3 + k]);
                if (boundary_half(tracer, t, k)) ++boundary;
            }
        patch.wedges = static_cast<int>(wedges.size());
        int faces = static_cast<int>(patch.tris.size());
        patch.euler = patch.wedges - (3 * faces - boundary) / 2 - boundary + faces;
        for (int t0 : patch.tris)
            for (int k0 = 0; k0 < 3; ++k0) {
                if (seen[t0 * 3 + k0] || !boundary_half(tracer, t0, k0)) continue;
                PatchLoop loop;
                int t = t0, k = k0;
                for (size_t guard = 0; guard < tri_count * 3; ++guard) {
                    seen[t * 3 + k] = 1;
                    loop.halves.push_back(t * 3 + k);
                    Occurrence occ;
                    occ.vertex = mesh.tris[t][(k + 1) % 3];
                    int ct = t, ck = (k + 1) % 3;
                    occ.wedge = layout.wedge[ct * 3 + ck];
                    occ.fan = corner_angle(mesh, ct, ck);
                    for (size_t spin = 0; spin < 256 && !boundary_half(tracer, ct, ck); ++spin) {
                        occ.fan_targets.push_back(mesh.tris[ct][(ck + 1) % 3]);
                        int o = mesh.across[ct][ck], co = 0;
                        for (int c = 0; c < 3; ++c) if (mesh.tris[o][c] == occ.vertex) co = c;
                        ct = o;
                        ck = co;
                        occ.fan += corner_angle(mesh, ct, ck);
                    }
                    occ.node = layout.node[occ.vertex] != 0;
                    loop.occ.push_back(occ);
                    t = ct;
                    k = ck;
                    if (t == t0 && k == k0) break;
                    if (seen[t * 3 + k]) break;
                }
                patch.loops.push_back(loop);
            }
    }
    // ángulo interior en cada nodo, corrigiendo el zigzag de las aristas con la forma de la línea a una distancia
    for (Patch& patch : layout.patches) {
        for (PatchLoop& loop : patch.loops) {
            const int count = static_cast<int>(loop.occ.size());
            auto vertex_at = [&](int i) { return loop.occ[((i % count) + count) % count].vertex; };
            for (int i = 0; i < count; ++i) {
                Occurrence& occ = loop.occ[i];
                if (!occ.node) continue;
                int v = occ.vertex;
                const Vec3& normal = tracer.frames.normal[v];
                auto smooth_point = [&](int direction) {
                    double walked = 0;
                    int prev = v;
                    for (int s = 1; s <= count; ++s) {
                        int idx = i + direction * s, at = vertex_at(idx);
                        double step = (mesh.points[at] - mesh.points[prev]).norm();
                        if (walked + step >= radius && step > 0)
                            return mesh.points[prev] + (mesh.points[at] - mesh.points[prev]) * ((radius - walked) / step);
                        walked += step;
                        if (loop.occ[((idx % count) + count) % count].node) return mesh.points[at];
                        prev = at;
                    }
                    return mesh.points[prev];
                };
                Vec3 in_local = flatten(mesh.points[vertex_at(i - 1)] - mesh.points[v], normal);
                Vec3 out_local = flatten(mesh.points[vertex_at(i + 1)] - mesh.points[v], normal);
                Vec3 in_smooth = flatten(smooth_point(-1) - mesh.points[v], normal);
                Vec3 out_smooth = flatten(smooth_point(+1) - mesh.points[v], normal);
                double clamp = PI / 3;
                double d_in = std::max(-clamp, std::min(clamp, signed_angle(in_local, in_smooth, normal)));
                double d_out = std::max(-clamp, std::min(clamp, signed_angle(out_local, out_smooth, normal)));
                occ.angle = occ.fan - d_out + d_in;
                occ.quarters = std::max(0, static_cast<int>(std::lround(occ.angle / (PI / 2))));
            }
        }
    }
    // en cada nodo, los cuartos de todas sus cuñas deben sumar lo que toca: 4 - k en el interior (k: índice del polo en
    // cuartos, 0 si no es polo) y el ángulo total redondeado en un borde. Reparto por restos mayores, para que una T
    // oblicua (135° + 45° contra 180°) quede como 1 + 1 + 2 y no como una esquina cóncava.
    {
        std::unordered_map<int, std::vector<Occurrence*>> by_vertex;
        for (Patch& patch : layout.patches)
            for (PatchLoop& loop : patch.loops)
                for (Occurrence& occ : loop.occ)
                    if (occ.node) by_vertex[occ.vertex].push_back(&occ);
        for (auto& item : by_vertex) {
            auto& list = item.second;
            double total = 0;
            for (Occurrence* occ : list) total += occ->angle;
            int wanted;
            if (tracer.border[item.first]) wanted = static_cast<int>(std::lround(total / (PI / 2)));
            else {
                auto pole = tracer.pole_quarters.find(item.first);
                wanted = 4 - (pole == tracer.pole_quarters.end() ? 0 : pole->second);
            }
            int given = 0;
            std::vector<std::pair<double, Occurrence*>> rest;
            for (Occurrence* occ : list) {
                double share = std::max(0.0, occ->angle / (PI / 2));
                occ->quarters = static_cast<int>(std::floor(share));
                given += occ->quarters;
                rest.push_back({share - occ->quarters, occ});
            }
            std::sort(rest.begin(), rest.end(), [](const std::pair<double, Occurrence*>& a, const std::pair<double, Occurrence*>& b) {
                return a.first > b.first;
            });
            for (size_t i = 0; given < wanted && !rest.empty(); i = (i + 1) % rest.size()) {
                ++rest[i].second->quarters;
                ++given;
            }
            for (size_t i = rest.size(); given > wanted && i-- > 0;) {
                if (rest[i].second->quarters > 0) {
                    --rest[i].second->quarters;
                    --given;
                }
                if (i == 0 && given > wanted) i = rest.size();
            }
        }
    }
    // esquinas dudosas: un parche con una esquina de más y otro con una de menos. La esquina sobrante se pasa de parche en
    // parche por nodos compartidos (en un nodo, una cuña deja de ser esquina y otra lo pasa a ser: la suma del nodo no
    // cambia) hasta llegar al que le falta, por el camino que menos fuerza los ángulos (Dijkstra).
    {
        const int patch_count = static_cast<int>(layout.patches.size());
        auto corner_units = [&](const Patch& patch) {
            if (patch.loops.size() != 1 || patch.euler != 1) return 4;
            int units = 0;
            for (const Occurrence& occ : patch.loops[0].occ)
                if (occ.node) {
                    if (occ.quarters >= 3) return 4;  // cóncavo: se arregla partiendo, no aquí
                    units += 2 - occ.quarters;
                }
            return units;
        };
        std::unordered_map<int, std::vector<std::pair<int, Occurrence*>>> by_vertex;
        for (int p = 0; p < patch_count; ++p)
            if (layout.patches[p].loops.size() == 1)
                for (Occurrence& occ : layout.patches[p].loops[0].occ)
                    if (occ.node) by_vertex[occ.vertex].push_back({p, &occ});
        std::vector<int> units(patch_count);
        for (int p = 0; p < patch_count; ++p) units[p] = corner_units(layout.patches[p]);
        auto penalty = [](const Occurrence* occ, int quarters) {
            double now = std::abs(occ->quarters * 90.0 - occ->angle * 180.0 / PI);
            double then = std::abs(quarters * 90.0 - occ->angle * 180.0 / PI);
            return then - now;
        };
        const double HOP = 20.0;  // cada parche intermedio cuesta como 20° de error
        for (int source = 0; source < patch_count; ++source) {
            while (units[source] > 4) {
                struct Step { int from = -1; Occurrence* give = nullptr; Occurrence* take = nullptr; };
                std::vector<double> dist(patch_count, 1e300);
                std::vector<Step> back(patch_count);
                std::vector<char> done(patch_count, 0);
                typedef std::pair<double, int> Item;
                std::priority_queue<Item, std::vector<Item>, std::greater<Item>> queue;
                dist[source] = 0;
                queue.push({0.0, source});
                int target = -1;
                while (!queue.empty()) {
                    Item item = queue.top();
                    queue.pop();
                    int at = item.second;
                    if (done[at]) continue;
                    done[at] = 1;
                    if (at != source && units[at] < 4) { target = at; break; }
                    if (item.first > 6 * HOP + 180.0) break;
                    for (Occurrence& give : layout.patches[at].loops[0].occ) {
                        if (!give.node || give.quarters > 1) continue;
                        // la cuña que cede no puede ser la misma por la que llegó la esquina a este parche
                        if (back[at].take == &give) continue;
                        for (auto& other : by_vertex[give.vertex]) {
                            int next = other.first;
                            Occurrence* take = other.second;
                            if (next == at || done[next] || take->quarters != 2) continue;
                            if (layout.patches[next].loops.size() != 1 || layout.patches[next].euler != 1) continue;
                            double cost = item.first + HOP + std::max(0.0, penalty(&give, give.quarters + 1)) +
                                          std::max(0.0, penalty(take, 1));
                            if (cost < dist[next]) {
                                dist[next] = cost;
                                back[next].from = at;
                                back[next].give = &give;
                                back[next].take = take;
                                queue.push({cost, next});
                            }
                        }
                    }
                }
                if (target < 0) break;
                for (int at = target; at != source; at = back[at].from) {
                    ++back[at].give->quarters;
                    --back[at].take->quarters;
                }
                --units[source];
                ++units[target];
            }
        }
    }
    // parche bueno: disco, sin nodos cóncavos, y cuatro esquinas contando una punta (dos líneas que se juntan) como dos
    // esquinas con un lado vacío entre ellas: ese parche es una tira de ancho cero que el reparto colapsa
    for (Patch& patch : layout.patches) {
        if (patch.loops.empty()) patch.reason = "cerrado";
        else if (patch.loops.size() != 1 || patch.euler != 1) patch.reason = "no_disco";
        else {
            const auto& occs = patch.loops[0].occ;
            for (size_t i = 0; i < occs.size(); ++i) {
                const Occurrence& occ = occs[i];
                if (!occ.node) continue;
                if (occ.quarters >= 3) patch.reason = "concavo";
                else {
                    patch.corners += 2 - occ.quarters;
                    if (occ.quarters <= 1 && (patch.first_corner < 0 || occs[patch.first_corner].quarters == 0 && occ.quarters == 1))
                        patch.first_corner = static_cast<int>(i);
                }
            }
            if (patch.reason.empty() && patch.corners != 4) patch.reason = patch.corners < 4 ? "pocas_esquinas" : "muchas_esquinas";
        }
        if (!patch.reason.empty()) {
            ++layout.reasons[patch.reason];
            continue;
        }
        patch.reason = build_sides(tracer, layout, patch);
        if (!patch.reason.empty()) {
            ++layout.reasons[patch.reason];
            continue;
        }
        patch.good = true;
        ++layout.good;
    }
    if (getenv("MBL_CORE_DEBUG")) {
        for (size_t p = 0; p < layout.patches.size(); ++p) {
            const Patch& patch = layout.patches[p];
            fprintf(stderr, "parche %d: %s tris=%d lazos=%d euler=%d esquinas=%d\n", static_cast<int>(p),
                    patch.good ? "bien" : patch.reason.c_str(), static_cast<int>(patch.tris.size()),
                    static_cast<int>(patch.loops.size()), patch.euler, patch.corners);
            for (const PatchLoop& loop : patch.loops) {
                fprintf(stderr, "   lazo %d:", static_cast<int>(loop.occ.size()));
                for (const Occurrence& occ : loop.occ)
                    if (occ.node) fprintf(stderr, " v%d(q%d %.0f/%.0f)", occ.vertex, occ.quarters, occ.angle * 180 / PI, occ.fan * 180 / PI);
                fprintf(stderr, "\n");
            }
        }
    }
    return layout;
}

// junta un parche de 3 esquinas con un vecino de 5 (casi siempre un triángulo diminuto junto a un polo, donde se cruzan
// tres líneas muy cerca) borrando el tramo que comparten; devuelve cuántos tramos se han borrado
static int merge_bad_patches(Tracer& tracer, const Layout& layout, double edge_length) {
    std::vector<char> handled(layout.patches.size(), 0);
    int removed = 0;
    auto remove_segment = [&](const Segment& seg) {
        for (int e : seg.edges) {
            if (!tracer.traced[e]) continue;
            tracer.traced[e] = 0;
            --tracer.degree[tracer.graph.ends[e][0]];
            --tracer.degree[tracer.graph.ends[e][1]];
        }
        ++removed;
    };
    // un parche malo diminuto (menos de un quad) se absorbe en el vecino por su tramo más largo
    {
        std::vector<double> area(layout.patches.size(), 0.0);
        for (size_t f = 0; f < layout.face_patch.size(); ++f) area[layout.face_patch[f]] += tracer.mesh->area[f];
        std::vector<int> best(layout.patches.size(), -1);
        for (size_t s = 0; s < layout.segments.size(); ++s) {
            const Segment& seg = layout.segments[s];
            bool fixed = false;
            for (int e : seg.edges) if (tracer.fixed_edge[e]) fixed = true;
            if (fixed || seg.edges.empty()) continue;
            const auto& faces = tracer.graph.faces[seg.edges[0]];
            if (faces[1] < 0) continue;
            int a = layout.face_patch[faces[0]], b = layout.face_patch[faces[1]];
            if (a == b) continue;
            for (int p : {a, b}) {
                const Patch& patch = layout.patches[p];
                if (patch.good || area[p] >= edge_length * edge_length) continue;
                if (best[p] < 0 || seg.length > layout.segments[best[p]].length) best[p] = static_cast<int>(s);
            }
        }
        for (size_t p = 0; p < layout.patches.size(); ++p) {
            if (best[p] < 0 || handled[p]) continue;
            const Segment& seg = layout.segments[best[p]];
            const auto& faces = tracer.graph.faces[seg.edges[0]];
            int a = layout.face_patch[faces[0]], b = layout.face_patch[faces[1]];
            if (handled[a] || handled[b]) continue;
            remove_segment(seg);
            handled[a] = handled[b] = 1;
        }
    }
    for (size_t s = 0; s < layout.segments.size(); ++s) {
        const Segment& seg = layout.segments[s];
        bool fixed = false;
        for (int e : seg.edges) if (tracer.fixed_edge[e]) fixed = true;
        if (fixed || seg.edges.empty()) continue;
        const auto& faces = tracer.graph.faces[seg.edges[0]];
        if (faces[1] < 0) continue;
        int a = layout.face_patch[faces[0]], b = layout.face_patch[faces[1]];
        if (a == b || handled[a] || handled[b]) continue;
        const Patch& pa = layout.patches[a];
        const Patch& pb = layout.patches[b];
        bool three_five = (pa.reason == "pocas_esquinas" && pb.reason == "muchas_esquinas") ||
                          (pa.reason == "muchas_esquinas" && pb.reason == "pocas_esquinas");
        if (!three_five) continue;
        remove_segment(seg);
        handled[a] = handled[b] = 1;
    }
    return removed;
}

// líneas colgantes: tramos con un extremo suelto (ni polo ni otra línea). Dentro de un parche son una raja que el
// contorno recorre por las dos caras; no separan nada y estropean el reparto. Se podan hasta que no quede ninguna.
static int prune_dangling(Tracer& tracer) {
    int removed = 0;
    const size_t n = tracer.degree.size();
    for (bool again = true; again;) {
        again = false;
        for (size_t v = 0; v < n; ++v) {
            if (tracer.degree[v] != 1 || tracer.pole_quarters.count(static_cast<int>(v))) continue;
            // seguir desde la punta hasta un vértice con otra cosa que una cadena
            int at = static_cast<int>(v), prev = -1;
            std::vector<int> edges;
            for (size_t guard = 0; guard < n; ++guard) {
                int next_edge = -1, next = -1;
                for (const auto& s : tracer.graph.around[at])
                    if (tracer.traced[s.second] && s.first != prev) { next_edge = s.second; next = s.first; break; }
                if (next_edge < 0 || tracer.fixed_edge[next_edge]) break;
                edges.push_back(next_edge);
                prev = at;
                at = next;
                if (tracer.degree[at] != 2 || tracer.pole_quarters.count(at)) break;
            }
            if (edges.empty()) continue;
            for (int e : edges) {
                tracer.traced[e] = 0;
                --tracer.degree[tracer.graph.ends[e][0]];
                --tracer.degree[tracer.graph.ends[e][1]];
            }
            ++removed;
            again = true;
        }
    }
    return removed;
}

// parte los parches malos con una línea nueva cada uno; devuelve cuántas se han empezado
static int split_bad_patches(Tracer& tracer, const Layout& layout) {
    const Mesh& mesh = *tracer.mesh;
    std::vector<Walker> walkers;
    // primer paso forzado dentro de la cuña del parche, para no salir a un parche vecino
    auto launch = [&](int v, const Occurrence& occ, const Vec3& wanted) {
        int best = -1;
        double best_cos = -2;
        for (int w : occ.fan_targets) {
            double c = unit(flatten(mesh.points[w] - mesh.points[v], tracer.frames.normal[v])).dot(unit(wanted));
            if (tracer.on_line[w] || tracer.stop[w]) c -= 1.0;  // mejor no ir directo a otra línea
            if (c > best_cos) { best_cos = c; best = w; }
        }
        if (best < 0) return false;
        int e = edge_between(tracer, v, best);
        if (e < 0 || tracer.traced[e]) return false;
        mark_edge(tracer, e);
        ++tracer.lines;
        Walker walker;
        walker.from = v;
        walker.at = best;
        walker.heading = unit(flatten(wanted, tracer.frames.normal[v]));
        if (tracer.stop[best] || tracer.on_line[best]) walker.alive = false;
        else tracer.on_line[best] = 1;
        walkers.push_back(walker);
        return true;
    };
    for (const Patch& patch : layout.patches) {
        if (patch.good || patch.reason == "lados" || patch.reason == "sin_tramo") continue;
        bool loose_pole = false;
        for (int t : patch.tris)
            for (int v : mesh.tris[t])
                if (tracer.degree[v] == 0 && tracer.pole_quarters.count(v)) loose_pole = true;
        if ((patch.reason == "pocas_esquinas" || patch.reason == "muchas_esquinas") && !loose_pole) continue;
        // un polo suelto dentro del parche: sus separatrices
        bool done = false;
        for (int t : patch.tris) {
            for (int v : mesh.tris[t])
                if (tracer.degree[v] == 0 && tracer.pole_quarters.count(v)) {
                    std::vector<Vec3> dirs = separatrices(tracer, v, tracer.pole_quarters[v]);
                    for (const Vec3& d : dirs) walkers.push_back(start_walker(tracer, v, d));
                    done = true;
                    break;
                }
            if (done) break;
        }
        if (done) continue;
        if (patch.loops.empty()) {
            // sin ningún borde (un toro sin polos): un lazo cerrado siguiendo la cruz
            int v = mesh.tris[patch.tris[0]][0];
            walkers.push_back(start_walker(tracer, v, cross_direction(tracer.frames, v, tracer.frames.tangent[v])));
            continue;
        }
        // un nodo cóncavo o en ángulo llano de más: seguir la línea que llega
        for (const PatchLoop& loop : patch.loops) {
            const int count = static_cast<int>(loop.occ.size());
            for (int i = 0; i < count && !done; ++i) {
                const Occurrence& occ = loop.occ[i];
                if (!occ.node || occ.quarters < 3) continue;
                int v = occ.vertex, prev = loop.occ[(i - 1 + count) % count].vertex;
                Vec3 wanted = cross_direction(tracer.frames, v, mesh.points[v] - mesh.points[prev]);
                done = launch(v, occ, wanted);
            }
            if (done) break;
        }
        if (done) continue;
        // si no: desde el medio del tramo más largo del contorno, hacia dentro
        const PatchLoop& loop = patch.loops.size() > 1 ? patch.loops[1] : patch.loops[0];
        const int count = static_cast<int>(loop.occ.size());
        int best_start = 0, best_length = -1, run = 0, run_start = 0;
        for (int s = 0; s < 2 * count; ++s) {
            int i = s % count;
            if (loop.occ[i].node) {
                if (run > best_length) { best_length = run; best_start = run_start; }
                run = 0;
                run_start = s + 1;
            } else {
                ++run;
            }
        }
        if (run > best_length) { best_length = run; best_start = run_start; }
        int pick = (best_start + std::max(0, best_length) / 2) % count;
        // vértice sin cuña útil (todo el abanico en líneas): probar los de al lado
        for (int tries = 0; tries < count && !done; ++tries) {
            int i = (pick + tries) % count;
            const Occurrence& occ = loop.occ[i];
            if (occ.fan_targets.empty()) continue;
            int v = occ.vertex, next = loop.occ[(i + 1) % count].vertex;
            Vec3 along = flatten(mesh.points[next] - mesh.points[v], tracer.frames.normal[v]);
            Vec3 left = tracer.frames.normal[v].cross(unit(along));
            done = launch(v, occ, cross_direction(tracer.frames, v, left));
        }
    }
    int started = static_cast<int>(walkers.size());
    run_walkers(tracer, walkers);
    return started;
}

// ---------------------------------------------------------------- S5: reparto de divisiones
//
// Cada tramo lleva un número entero de aristas de la malla final. En cada parche, los lados opuestos deben sumar lo mismo.
// Se busca lo más parecido a longitud / arista objetivo: mínimos cuadrados con esas igualdades, y redondeo por tandas
// (primero lo que ya está casi entero), volviendo a resolver el resto cada vez.

struct QuantReport {
    int variables = 0, constraints = 0, violations = 0, solves = 0, collapsed = 0, repairs = 0;
    double mean_error = 0;
};

static QuantReport quantize(Layout& layout, double edge_length) {
    QuantReport report;
    const int count = static_cast<int>(layout.segments.size());
    std::vector<double> target(count), weight(count);
    for (int s = 0; s < count; ++s) {
        target[s] = std::max(layout.segments[s].length / edge_length, 0.05);
        weight[s] = 1.0 / std::max(target[s], 0.5);
    }
    // filas: lado 0 - lado 2, lado 1 - lado 3 de cada parche bueno
    std::vector<std::vector<std::pair<int, double>>> rows;
    for (const Patch& patch : layout.patches) {
        if (!patch.good) continue;
        for (int pair = 0; pair < 2; ++pair) {
            std::map<int, double> row;
            for (const auto& item : patch.sides[pair]) row[item.first] += 1.0;
            for (const auto& item : patch.sides[pair + 2]) row[item.first] -= 1.0;
            std::vector<std::pair<int, double>> clean;
            for (const auto& item : row) if (item.second != 0) clean.push_back(item);
            if (!clean.empty()) rows.push_back(clean);
        }
    }
    report.constraints = static_cast<int>(rows.size());
    std::vector<std::vector<std::pair<int, double>>> columns(count);
    for (int r = 0; r < static_cast<int>(rows.size()); ++r)
        for (const auto& item : rows[r]) columns[item.first].push_back({r, item.second});
    std::vector<char> fixed(count, 0);
    std::vector<double> value(target);
    int used = 0;
    for (int s = 0; s < count; ++s) if (!columns[s].empty()) ++used;
    report.variables = used;
    // los tramos sin ninguna igualdad: redondeo directo
    for (int s = 0; s < count; ++s)
        if (columns[s].empty()) { fixed[s] = 1; value[s] = std::max(1.0, std::round(target[s])); }
    const int nrows = static_cast<int>(rows.size());
    auto solve = [&]() {
        // M λ = A_F t_F + A_X x_X,   M = A_F W^-1 A_F^T;   x_F = t_F - W^-1 A_F^T λ
        ++report.solves;
        std::vector<double> rhs(nrows, 0.0), lambda(nrows, 0.0);
        std::vector<char> active(nrows, 0);
        for (int r = 0; r < nrows; ++r)
            for (const auto& item : rows[r]) {
                rhs[r] += item.second * (fixed[item.first] ? value[item.first] : target[item.first]);
                if (!fixed[item.first]) active[r] = 1;
            }
        for (int r = 0; r < nrows; ++r) if (!active[r]) rhs[r] = 0;
        auto apply = [&](const std::vector<double>& y, std::vector<double>& out) {
            std::vector<double> g(count, 0.0);
            for (int r = 0; r < nrows; ++r)
                if (active[r]) for (const auto& item : rows[r]) if (!fixed[item.first]) g[item.first] += item.second * y[r];
            out.assign(nrows, 0.0);
            for (int r = 0; r < nrows; ++r) {
                if (!active[r]) continue;
                for (const auto& item : rows[r]) if (!fixed[item.first]) out[r] += item.second * g[item.first] / weight[item.first];
                out[r] += 1e-9 * y[r];  // regularización: si las filas fijadas chocan, solución de mínimos cuadrados en vez de divergir
            }
        };
        std::vector<double> residual(rhs), direction(rhs), product;
        double rr = 0;
        for (double x : residual) rr += x * x;
        const double stop = 1e-20 * std::max(1.0, rr);
        for (int it = 0; it < 2000 && rr > stop; ++it) {
            apply(direction, product);
            double dap = 0;
            for (int r = 0; r < nrows; ++r) dap += direction[r] * product[r];
            if (dap <= 1e-300) break;
            double alpha = rr / dap, next_rr = 0;
            for (int r = 0; r < nrows; ++r) {
                lambda[r] += alpha * direction[r];
                residual[r] -= alpha * product[r];
                next_rr += residual[r] * residual[r];
            }
            for (int r = 0; r < nrows; ++r) direction[r] = residual[r] + (next_rr / rr) * direction[r];
            rr = next_rr;
        }
        for (int s = 0; s < count; ++s) {
            if (fixed[s]) continue;
            double g = 0;
            for (const auto& item : columns[s]) g += item.second * lambda[item.first];
            value[s] = target[s] - g / weight[s];
        }
    };
    for (int round = 0; round < 10000; ++round) {
        std::vector<int> free_vars;
        for (int s = 0; s < count; ++s) if (!fixed[s]) free_vars.push_back(s);
        if (free_vars.empty()) break;
        solve();
        std::sort(free_vars.begin(), free_vars.end(), [&](int a, int b) {
            double fa = std::abs(value[a] - std::max(0.0, std::round(value[a])));
            double fb = std::abs(value[b] - std::max(0.0, std::round(value[b])));
            return fa != fb ? fa < fb : a < b;
        });
        size_t batch = std::max<size_t>(1, free_vars.size() / 10);
        for (size_t i = 0; i < batch; ++i) {
            int s = free_vars[i];
            fixed[s] = 1;
            value[s] = std::max(0.0, std::round(value[s]));
        }
    }
    // reparación: un parche cuyos lados opuestos no suman igual. Sumar 1 a un tramo del lado corto obliga al vecino por
    // ese tramo a sumar 1 en su lado opuesto, y así hasta un borde, un parche sin reparto o un parche al que le falte
    // justo eso: es meter (o quitar) una fila de quads. Se busca la fila más barata con Dijkstra.
    {
        std::vector<int> x(count);
        for (int s = 0; s < count; ++s) x[s] = static_cast<int>(std::lround(value[s]));
        // dónde está cada tramo: (parche, lado)
        std::vector<std::vector<std::pair<int, int>>> place(count);
        std::vector<int> good_patches;
        for (int p = 0; p < static_cast<int>(layout.patches.size()); ++p) {
            const Patch& patch = layout.patches[p];
            if (!patch.good) continue;
            good_patches.push_back(p);
            for (int side = 0; side < 4; ++side)
                for (const auto& item : patch.sides[side]) place[item.first].push_back({p, side});
        }
        auto side_sum = [&](int p, int side) {
            int sum = 0;
            for (const auto& item : layout.patches[p].sides[side]) sum += x[item.first];
            return sum;
        };
        auto step_cost = [&](int s, int delta) {
            double a = x[s] - target[s], b = x[s] + delta - target[s];
            return std::max(0.0, (b * b - a * a) * weight[s]) + 1e-3;  // sin costes negativos: Dijkstra
        };
        if (getenv("MBL_CORE_DEBUG")) {
            int broken_count = 0, units = 0;
            for (int p : good_patches)
                for (int pair = 0; pair < 2; ++pair) {
                    int d = std::abs(side_sum(p, pair) - side_sum(p, pair + 2));
                    if (d) { ++broken_count; units += d; }
                }
            double residual = 0;
            for (const auto& row : rows) {
                double r = 0;
                for (const auto& item : row) r += item.second * value[item.first];
                residual += std::abs(r);
            }
            fprintf(stderr, "reparto: %d lados descuadrados, %d unidades; residuo antes de redondear %.3f\n", broken_count, units, residual);
        }
        for (int guard = 0; guard < 4 * static_cast<int>(good_patches.size()) + 16; ++guard) {
            // primer parche roto: le falta `need` en el lado `side`
            int broken = -1, side = -1;
            for (int p : good_patches) {
                for (int pair = 0; pair < 2 && broken < 0; ++pair) {
                    int a = side_sum(p, pair), b = side_sum(p, pair + 2);
                    if (a < b) { broken = p; side = pair; }
                    else if (b < a) { broken = p; side = pair + 2; }
                }
                if (broken >= 0) break;
            }
            if (broken < 0) break;
            // dos opciones: +1 en el lado corto, o -1 en el largo
            double best_cost = 1e300;
            std::vector<std::pair<int, int>> best_path;
            for (int delta : {+1, -1}) {
                int start_side = delta > 0 ? side : (side + 2) % 4;
                // estado: (parche, lado) donde hace falta delta; nodo = p * 4 + lado
                const int states = static_cast<int>(layout.patches.size()) * 4;
                std::vector<double> dist(states, 1e300);
                std::vector<std::pair<int, int>> back(states, {-1, -1});  // (estado previo, tramo)
                typedef std::pair<double, int> Item;
                std::priority_queue<Item, std::vector<Item>, std::greater<Item>> queue;
                int start = broken * 4 + start_side;
                dist[start] = 0;
                queue.push({0.0, start});
                int finish = -1, finish_segment = -1;
                double finish_cost = 1e300;
                while (!queue.empty()) {
                    Item item = queue.top();
                    queue.pop();
                    if (item.first > dist[item.second] || item.first >= finish_cost) continue;
                    int p = item.second / 4, k = item.second % 4;
                    for (const auto& seg_item : layout.patches[p].sides[k]) {
                        int s = seg_item.first;
                        if (x[s] + delta < 0) continue;
                        double cost = item.first + step_cost(s, delta);
                        // el otro parche que toca este tramo
                        int other = -1, other_side = -1;
                        bool twice = false;
                        int here_count = 0;
                        for (const auto& where : place[s]) {
                            if (where.first == p) ++here_count;
                            if (where.first == p && where.second == k) continue;
                            if (where.first == p) twice = true;
                            other = where.first;
                            other_side = where.second;
                        }
                        if (twice || here_count > 1) continue;  // el tramo cuenta dos veces en este parche
                        if (other < 0) {
                            // borde o parche sin reparto: aquí acaba la fila
                            if (cost < finish_cost) { finish_cost = cost; finish = item.second; finish_segment = s; }
                            continue;
                        }
                        // ¿al otro le arregla algo? (le falta delta justo en ese lado)
                        int here = side_sum(other, other_side), there = side_sum(other, (other_side + 2) % 4);
                        if ((delta > 0 && here < there) || (delta < 0 && here > there)) {
                            if (cost < finish_cost) { finish_cost = cost; finish = item.second; finish_segment = s; }
                            continue;
                        }
                        int next = other * 4 + (other_side + 2) % 4;
                        if (cost < dist[next]) {
                            dist[next] = cost;
                            back[next] = {item.second, s};
                            queue.push({cost, next});
                        }
                    }
                }
                if (finish < 0 || finish_cost >= best_cost) continue;
                best_cost = finish_cost;
                best_path.clear();
                best_path.push_back({finish_segment, delta});
                for (int at = finish; at != start; at = back[at].first) best_path.push_back({back[at].second, delta});
            }
            if (best_path.empty()) {
                // sin arreglo: el parche se queda fuera del reparto (no se rellena)
                layout.patches[broken].good = false;
                layout.patches[broken].reason = "reparto";
                ++layout.reasons["reparto"];
                --layout.good;
                ++report.violations;
                good_patches.erase(std::find(good_patches.begin(), good_patches.end(), broken));
                for (auto& list : place)
                    list.erase(std::remove_if(list.begin(), list.end(), [&](const std::pair<int, int>& w) { return w.first == broken; }), list.end());
                continue;
            }
            auto imbalance = [&]() {
                int units = 0;
                for (int q : good_patches) for (int pair = 0; pair < 2; ++pair) units += std::abs(side_sum(q, pair) - side_sum(q, pair + 2));
                return units;
            };
            int before = imbalance();
            for (const auto& item : best_path) x[item.first] += item.second;
            if (imbalance() >= before) {
                // la fila no arregla nada (un tramo que el parche ve dos veces, o similar): se deshace y el parche sale
                for (const auto& item : best_path) x[item.first] -= item.second;
                best_path.clear();
            }
            if (best_path.empty()) {
                layout.patches[broken].good = false;
                layout.patches[broken].reason = "reparto";
                ++layout.reasons["reparto"];
                --layout.good;
                ++report.violations;
                good_patches.erase(std::find(good_patches.begin(), good_patches.end(), broken));
                for (auto& list : place)
                    list.erase(std::remove_if(list.begin(), list.end(), [&](const std::pair<int, int>& w) { return w.first == broken; }), list.end());
                continue;
            }
            ++report.repairs;
        }
        if (getenv("MBL_CORE_DEBUG")) {
            int units = 0;
            for (int p : good_patches) for (int pair = 0; pair < 2; ++pair) units += std::abs(side_sum(p, pair) - side_sum(p, pair + 2));
            fprintf(stderr, "reparto: tras reparar %d filas quedan %d unidades\n", report.repairs, units);
        }
        for (int s = 0; s < count; ++s) value[s] = x[s];
    }
    double error = 0;
    for (int s = 0; s < count; ++s) {
        layout.segments[s].divisions = static_cast<int>(std::lround(value[s]));
        error += std::abs(value[s] - target[s]) / std::max(target[s], 1.0);
    }
    report.mean_error = count ? error / count : 0.0;
    for (Patch& patch : layout.patches) {
        if (!patch.good) continue;
        int sums[4] = {0, 0, 0, 0};
        for (int side = 0; side < 4; ++side)
            for (const auto& item : patch.sides[side]) sums[side] += layout.segments[item.first].divisions;
        if (sums[0] != sums[2] || sums[1] != sums[3]) {
            ++report.violations;
            patch.good = false;
            patch.reason = "reparto";
            ++layout.reasons["reparto"];
            --layout.good;
        } else if (sums[0] == 0 || sums[1] == 0) {
            patch.degenerate = true;
            ++report.collapsed;
        }
    }
    return report;
}

// ---------------------------------------------------------------- S6: relleno de cada parche con su rejilla
//
// El parche (un disco de cuatro lados) se lleva a un rectángulo W x H con un mapa de Tutte de pesos positivos (siempre sin
// pliegues, Floater 2003); cada punto entero del rectángulo es un vértice de la malla final. Los vértices de los lados se
// reparten por longitud a lo largo de cada tramo, así que dos parches vecinos comparten exactamente los mismos.

struct QuadMesh {
    std::vector<Vec3> points;
    std::vector<char> pinned;  // sobre un canto o un borde: el relajado no lo mueve
    std::vector<std::array<int, 4>> quads;
    int patches_filled = 0, patches_collapsed = 0, quads_dropped = 0;
};

static void fill_patches(const Tracer& tracer, const Layout& layout, QuadMesh& out) {
    const Mesh& mesh = *tracer.mesh;
    std::vector<int> node_out(mesh.points.size(), -1);
    std::vector<int> segment_base(layout.segments.size(), -1);
    auto node_vertex = [&](int v) {
        if (node_out[v] < 0) {
            node_out[v] = static_cast<int>(out.points.size());
            out.points.push_back(mesh.points[v]);
        }
        return node_out[v];
    };
    // punto k (de 1 a divisiones - 1) de un tramo, en su orden guardado
    auto segment_vertex = [&](int s, int k) {
        const Segment& seg = layout.segments[s];
        if (segment_base[s] < 0) {
            segment_base[s] = static_cast<int>(out.points.size());
            double walked = 0;
            size_t j = 0;
            for (int i = 1; i < seg.divisions; ++i) {
                double goal = seg.length * i / seg.divisions;
                while (j + 1 < seg.verts.size()) {
                    double step = (mesh.points[seg.verts[j + 1]] - mesh.points[seg.verts[j]]).norm();
                    if (walked + step >= goal) {
                        double f = step > 0 ? (goal - walked) / step : 0.0;
                        out.points.push_back(mesh.points[seg.verts[j]] + (mesh.points[seg.verts[j + 1]] - mesh.points[seg.verts[j]]) * f);
                        break;
                    }
                    walked += step;
                    ++j;
                }
                if (j + 1 >= seg.verts.size()) out.points.push_back(mesh.points[seg.verts.back()]);
            }
        }
        return segment_base[s] + k - 1;
    };
    std::vector<int> joined;
    auto root = [&](int x) {
        while (joined[x] != x) x = joined[x] = joined[joined[x]];
        return x;
    };
    auto unite = [&](int a, int b) {
        if (static_cast<int>(joined.size()) < static_cast<int>(out.points.size())) {
            size_t old = joined.size();
            joined.resize(out.points.size());
            for (size_t i = old; i < joined.size(); ++i) joined[i] = static_cast<int>(i);
        }
        a = root(a);
        b = root(b);
        if (a != b) joined[std::max(a, b)] = std::min(a, b);
    };
    // los tramos de cero divisiones juntan sus dos nodos
    for (const Segment& seg : layout.segments)
        if (seg.divisions == 0) unite(node_vertex(seg.a), node_vertex(seg.b));
    std::vector<int> local(mesh.tris.size() * 3, -1);
    for (const Patch& patch : layout.patches) {
        if (!patch.good) continue;
        int W = 0, H = 0;
        for (const auto& item : patch.sides[0]) W += layout.segments[item.first].divisions;
        for (const auto& item : patch.sides[1]) H += layout.segments[item.first].divisions;
        // vértice de la malla final en la posición p (entera) de un lado
        auto side_vertex = [&](int side, int p) {
            if (patch.sides[side].empty()) return node_vertex(patch.side_start[side]);
            int cum = 0;
            for (const auto& item : patch.sides[side]) {
                const Segment& seg = layout.segments[item.first];
                if (p == cum) return node_vertex(item.second ? seg.b : seg.a);
                if (seg.divisions == 0) continue;
                if (p < cum + seg.divisions) {
                    int k = p - cum;
                    return segment_vertex(item.first, item.second ? seg.divisions - k : k);
                }
                cum += seg.divisions;
            }
            const auto& last = patch.sides[side].back();
            (void)last;
            const Segment& seg = layout.segments[last.first];
            return node_vertex(last.second ? seg.a : seg.b);
        };
        if (patch.degenerate) {
            // tira de ancho cero: se cosen sus lados opuestos punto a punto
            if (H == 0) for (int i = 0; i <= W; ++i) unite(side_vertex(0, i), side_vertex(2, W - i));
            if (W == 0) for (int j = 0; j <= H; ++j) unite(side_vertex(1, j), side_vertex(3, H - j));
            ++out.patches_collapsed;
            continue;
        }
        // cuñas del parche: índices locales; las del contorno con su posición en el rectángulo
        std::vector<int> wedge_ids;
        for (int t : patch.tris)
            for (int k = 0; k < 3; ++k) {
                int w = layout.wedge[t * 3 + k];
                if (local[w] < 0) { local[w] = static_cast<int>(wedge_ids.size()); wedge_ids.push_back(w); }
            }
        const int nv = static_cast<int>(wedge_ids.size());
        std::vector<double> u(nv, 0.0), v(nv, 0.0);
        std::vector<char> pinned(nv, 0);
        std::vector<Vec3> where(nv);
        for (int t : patch.tris)
            for (int k = 0; k < 3; ++k) where[local[layout.wedge[t * 3 + k]]] = mesh.points[mesh.tris[t][k]];
        {
            const PatchLoop& loop = patch.loops[0];
            const int count = static_cast<int>(loop.halves.size());
            int side = 0, cum = 0;
            double arc = 0;
            int corner = patch.first_corner;
            auto place = [&](int id, int s, double pos) {
                pinned[id] = 1;
                if (s == 0) { u[id] = pos; v[id] = 0; }
                else if (s == 1) { u[id] = W; v[id] = pos; }
                else if (s == 2) { u[id] = W - pos; v[id] = H; }
                else { u[id] = 0; v[id] = H - pos; }
            };
            place(local[loop.occ[corner].wedge], 0, 0.0);
            for (int s = 1; s <= count; ++s) {
                int i = (corner + s) % count;
                int half = loop.halves[i];
                const Segment& seg = layout.segments[layout.edge_segment[tracer.graph.side_edge[half]].first];
                int t = half / 3, k = half % 3;
                arc += (mesh.points[mesh.tris[t][(k + 1) % 3]] - mesh.points[mesh.tris[t][k]]).norm();
                const Occurrence& occ = loop.occ[i];
                if (occ.node) {
                    cum += seg.divisions;
                    arc = 0;
                    if (occ.quarters == 1 && s < count) {
                        ++side;
                        cum = 0;
                    }
                    place(local[occ.wedge], side, cum);
                } else {
                    place(local[occ.wedge], side, cum + seg.divisions * std::min(1.0, arc / std::max(seg.length, 1e-30)));
                }
            }
        }
        // Laplaciano de cotangentes recortadas a positivas
        std::vector<std::vector<std::pair<int, double>>> neighbors(nv);
        for (int t : patch.tris)
            for (int k = 0; k < 3; ++k) {
                int a = local[layout.wedge[t * 3 + k]], b = local[layout.wedge[t * 3 + (k + 1) % 3]];
                double angle = corner_angle(mesh, t, (k + 2) % 3);
                double w = 0.5 * std::cos(angle) / std::max(std::sin(angle), 1e-12);
                neighbors[a].push_back({b, w});
                neighbors[b].push_back({a, w});
            }
        for (auto& list : neighbors) {
            std::sort(list.begin(), list.end());
            std::vector<std::pair<int, double>> merged;
            for (const auto& item : list) {
                if (!merged.empty() && merged.back().first == item.first) merged.back().second += item.second;
                else merged.push_back(item);
            }
            for (auto& item : merged) item.second = std::max(item.second, 0.01);
            list.swap(merged);
        }
        for (double* coord : {u.data(), v.data()}) {
            // CG sobre las cuñas libres, con precondicionador diagonal
            std::vector<double> diag(nv, 0.0), rhs(nv, 0.0), x(nv, 0.0);
            for (int i = 0; i < nv; ++i) {
                if (pinned[i]) continue;
                for (const auto& item : neighbors[i]) {
                    diag[i] += item.second;
                    if (pinned[item.first]) rhs[i] += item.second * coord[item.first];
                }
                x[i] = 0.5 * (coord == u.data() ? W : H);
            }
            auto apply = [&](const std::vector<double>& y, std::vector<double>& outv) {
                outv.assign(nv, 0.0);
                for (int i = 0; i < nv; ++i) {
                    if (pinned[i]) continue;
                    double sum = diag[i] * y[i];
                    for (const auto& item : neighbors[i]) if (!pinned[item.first]) sum -= item.second * y[item.first];
                    outv[i] = sum;
                }
            };
            std::vector<double> r(nv), z(nv), p(nv), q;
            apply(x, q);
            double rz = 0, rr0 = 0;
            for (int i = 0; i < nv; ++i) {
                r[i] = pinned[i] ? 0.0 : rhs[i] - q[i];
                z[i] = pinned[i] ? 0.0 : r[i] / diag[i];
                p[i] = z[i];
                rz += r[i] * z[i];
                rr0 += rhs[i] * rhs[i];
            }
            for (int it = 0; it < 4 * nv + 100; ++it) {
                double rr = 0;
                for (int i = 0; i < nv; ++i) rr += r[i] * r[i];
                if (rr <= 1e-22 * std::max(rr0, 1e-30)) break;
                apply(p, q);
                double pq = 0;
                for (int i = 0; i < nv; ++i) pq += p[i] * q[i];
                if (pq <= 0) break;
                double alpha = rz / pq, next_rz = 0;
                for (int i = 0; i < nv; ++i) {
                    if (pinned[i]) continue;
                    x[i] += alpha * p[i];
                    r[i] -= alpha * q[i];
                    z[i] = r[i] / diag[i];
                    next_rz += r[i] * z[i];
                }
                for (int i = 0; i < nv; ++i) p[i] = z[i] + (next_rz / rz) * p[i];
                rz = next_rz;
            }
            for (int i = 0; i < nv; ++i) if (!pinned[i]) coord[i] = x[i];
        }
        // puntos enteros interiores: en qué triángulo caen
        std::vector<std::vector<int>> cells(static_cast<size_t>(W + 1) * (H + 1));
        for (int t : patch.tris) {
            double lo_u = 1e300, hi_u = -1e300, lo_v = 1e300, hi_v = -1e300;
            for (int k = 0; k < 3; ++k) {
                int id = local[layout.wedge[t * 3 + k]];
                lo_u = std::min(lo_u, u[id]); hi_u = std::max(hi_u, u[id]);
                lo_v = std::min(lo_v, v[id]); hi_v = std::max(hi_v, v[id]);
            }
            int i0 = std::max(1, static_cast<int>(std::ceil(lo_u - 1e-9))), i1 = std::min(W - 1, static_cast<int>(std::floor(hi_u + 1e-9)));
            int j0 = std::max(1, static_cast<int>(std::ceil(lo_v - 1e-9))), j1 = std::min(H - 1, static_cast<int>(std::floor(hi_v + 1e-9)));
            for (int i = i0; i <= i1; ++i)
                for (int j = j0; j <= j1; ++j) cells[static_cast<size_t>(i) * (H + 1) + j].push_back(t);
        }
        std::vector<int> grid(static_cast<size_t>(W + 1) * (H + 1), -1);
        for (int i = 0; i <= W; ++i)
            for (int j = 0; j <= H; ++j) {
                int& id = grid[static_cast<size_t>(i) * (H + 1) + j];
                if (j == 0) id = side_vertex(0, i);
                else if (i == W) id = side_vertex(1, j);
                else if (j == H) id = side_vertex(2, W - i);
                else if (i == 0) id = side_vertex(3, H - j);
                else {
                    double best = -1e300;
                    Vec3 point;
                    for (int t : cells[static_cast<size_t>(i) * (H + 1) + j]) {
                        int a = local[layout.wedge[t * 3]], b = local[layout.wedge[t * 3 + 1]], c = local[layout.wedge[t * 3 + 2]];
                        double det = (u[b] - u[a]) * (v[c] - v[a]) - (u[c] - u[a]) * (v[b] - v[a]);
                        if (std::abs(det) < 1e-300) continue;
                        double l1 = ((i - u[a]) * (v[c] - v[a]) - (u[c] - u[a]) * (j - v[a])) / det;
                        double l2 = ((u[b] - u[a]) * (j - v[a]) - (i - u[a]) * (v[b] - v[a])) / det;
                        double l0 = 1 - l1 - l2, worst = std::min(l0, std::min(l1, l2));
                        if (worst > best) {
                            best = worst;
                            point = where[a] * l0 + where[b] * l1 + where[c] * l2;
                        }
                    }
                    if (best == -1e300) {
                        // sin triángulo en la celda (no debería pasar): el vértice de la malla más cercano en el plano
                        double closest = 1e300;
                        for (int w = 0; w < nv; ++w) {
                            double d = (u[w] - i) * (u[w] - i) + (v[w] - j) * (v[w] - j);
                            if (d < closest) { closest = d; point = where[w]; }
                        }
                    }
                    id = static_cast<int>(out.points.size());
                    out.points.push_back(point);
                }
            }
        for (int i = 0; i < W; ++i)
            for (int j = 0; j < H; ++j)
                out.quads.push_back({{grid[static_cast<size_t>(i) * (H + 1) + j], grid[static_cast<size_t>(i + 1) * (H + 1) + j],
                                      grid[static_cast<size_t>(i + 1) * (H + 1) + j + 1], grid[static_cast<size_t>(i) * (H + 1) + j + 1]}});
        for (int w : wedge_ids) local[w] = -1;
        ++out.patches_filled;
    }
    // fijos: nodos y puntos de tramos que están sobre cantos o bordes
    std::vector<char> pinned(out.points.size(), 0);
    {
        std::vector<char> on_feature(mesh.points.size(), 0);
        for (size_t e = 0; e < tracer.fixed_edge.size(); ++e)
            if (tracer.fixed_edge[e]) on_feature[tracer.graph.ends[e][0]] = on_feature[tracer.graph.ends[e][1]] = 1;
        for (size_t v = 0; v < mesh.points.size(); ++v)
            if (node_out[v] >= 0 && on_feature[v]) pinned[node_out[v]] = 1;
        for (size_t s = 0; s < layout.segments.size(); ++s) {
            const Segment& seg = layout.segments[s];
            if (segment_base[s] < 0 || seg.edges.empty() || !tracer.fixed_edge[seg.edges[0]]) continue;
            for (int k = 1; k < seg.divisions; ++k) pinned[segment_base[s] + k - 1] = 1;
        }
    }
    // aplicar las uniones: posición media de cada grupo, y fuera los quads que se quedan sin área
    {
        size_t old = joined.size();
        joined.resize(out.points.size());
        for (size_t i = old; i < joined.size(); ++i) joined[i] = static_cast<int>(i);
    }
    std::vector<int> remap(out.points.size(), -1), members(out.points.size(), 0);
    std::vector<Vec3> sum(out.points.size());
    for (size_t i = 0; i < out.points.size(); ++i) {
        int r = root(static_cast<int>(i));
        sum[r] = sum[r] + out.points[i];
        ++members[r];
    }
    std::vector<Vec3> points;
    std::vector<char> group_pinned(out.points.size(), 0);
    for (size_t i = 0; i < out.points.size(); ++i)
        if (pinned[i]) group_pinned[root(static_cast<int>(i))] = 1;
    for (size_t i = 0; i < out.points.size(); ++i)
        if (root(static_cast<int>(i)) == static_cast<int>(i)) {
            remap[i] = static_cast<int>(points.size());
            points.push_back(sum[i] * (1.0 / members[i]));
            out.pinned.push_back(group_pinned[i]);
        }
    std::vector<std::array<int, 4>> quads;
    for (auto q : out.quads) {
        for (int& id : q) id = remap[root(id)];
        std::set<int> distinct(q.begin(), q.end());
        if (distinct.size() < 4) { ++out.quads_dropped; continue; }
        quads.push_back(q);
    }
    out.points.swap(points);
    out.quads.swap(quads);
}

// ---------------------------------------------------------------- S7: relajado
//
// Cada vértice libre se acerca a la media de sus vecinos solo en el plano tangente, y se vuelve a poner sobre la
// superficie de entrada (el punto más cercano, buscado en una rejilla de triángulos). Los de cantos y bordes no se mueven.

struct SurfaceGrid {
    const Mesh* mesh = nullptr;
    double cell = 1;
    Vec3 low;
    std::unordered_map<int64_t, std::vector<int>> cells;
    int64_t key(int i, int j, int k) const { return (static_cast<int64_t>(i) * 73856093) ^ (static_cast<int64_t>(j) * 19349663) ^ (static_cast<int64_t>(k) * 83492791); }
};

static SurfaceGrid surface_grid(const Mesh& mesh, double cell) {
    SurfaceGrid grid;
    grid.mesh = &mesh;
    grid.cell = cell;
    grid.low = mesh.points[0];
    for (const Vec3& q : mesh.points) grid.low = Vec3(std::min(grid.low.x, q.x), std::min(grid.low.y, q.y), std::min(grid.low.z, q.z));
    for (int f = 0; f < static_cast<int>(mesh.tris.size()); ++f) {
        Vec3 lo = mesh.points[mesh.tris[f][0]], hi = lo;
        for (int v : mesh.tris[f]) {
            const Vec3& q = mesh.points[v];
            lo = Vec3(std::min(lo.x, q.x), std::min(lo.y, q.y), std::min(lo.z, q.z));
            hi = Vec3(std::max(hi.x, q.x), std::max(hi.y, q.y), std::max(hi.z, q.z));
        }
        int i0 = static_cast<int>(std::floor((lo.x - grid.low.x) / cell)), i1 = static_cast<int>(std::floor((hi.x - grid.low.x) / cell));
        int j0 = static_cast<int>(std::floor((lo.y - grid.low.y) / cell)), j1 = static_cast<int>(std::floor((hi.y - grid.low.y) / cell));
        int k0 = static_cast<int>(std::floor((lo.z - grid.low.z) / cell)), k1 = static_cast<int>(std::floor((hi.z - grid.low.z) / cell));
        for (int i = i0; i <= i1; ++i)
            for (int j = j0; j <= j1; ++j)
                for (int k = k0; k <= k1; ++k) grid.cells[grid.key(i, j, k)].push_back(f);
    }
    return grid;
}

// punto del triángulo más cercano a p (Ericson, "Real-Time Collision Detection", 5.1.5)
static Vec3 closest_on_triangle(const Vec3& p, const Vec3& a, const Vec3& b, const Vec3& c) {
    Vec3 ab = b - a, ac = c - a, ap = p - a;
    double d1 = ab.dot(ap), d2 = ac.dot(ap);
    if (d1 <= 0 && d2 <= 0) return a;
    Vec3 bp = p - b;
    double d3 = ab.dot(bp), d4 = ac.dot(bp);
    if (d3 >= 0 && d4 <= d3) return b;
    double vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0) return a + ab * (d1 / (d1 - d3));
    Vec3 cp = p - c;
    double d5 = ab.dot(cp), d6 = ac.dot(cp);
    if (d6 >= 0 && d5 <= d6) return c;
    double vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0) return a + ac * (d2 / (d2 - d6));
    double va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)));
    double denom = 1.0 / (va + vb + vc);
    return a + ab * (vb * denom) + ac * (vc * denom);
}

static bool project_to_surface(const SurfaceGrid& grid, const Vec3& p, Vec3& out) {
    const Mesh& mesh = *grid.mesh;
    int ci = static_cast<int>(std::floor((p.x - grid.low.x) / grid.cell));
    int cj = static_cast<int>(std::floor((p.y - grid.low.y) / grid.cell));
    int ck = static_cast<int>(std::floor((p.z - grid.low.z) / grid.cell));
    double best = 1e300;
    for (int i = ci - 1; i <= ci + 1; ++i)
        for (int j = cj - 1; j <= cj + 1; ++j)
            for (int k = ck - 1; k <= ck + 1; ++k) {
                auto found = grid.cells.find(grid.key(i, j, k));
                if (found == grid.cells.end()) continue;
                for (int f : found->second) {
                    const auto& tri = mesh.tris[f];
                    Vec3 q = closest_on_triangle(p, mesh.points[tri[0]], mesh.points[tri[1]], mesh.points[tri[2]]);
                    double d = (q - p).dot(q - p);
                    if (d < best) { best = d; out = q; }
                }
            }
    return best < 1e300;
}

static const int RELAX_ROUNDS = 8;

static void relax_quads(const Mesh& mesh, QuadMesh& quads, double edge_length) {
    const size_t n = quads.points.size();
    std::vector<std::vector<int>> neighbors(n);
    std::vector<Vec3> normal(n);
    for (const auto& q : quads.quads)
        for (int k = 0; k < 4; ++k) {
            neighbors[q[k]].push_back(q[(k + 1) % 4]);
            neighbors[q[k]].push_back(q[(k + 3) % 4]);
        }
    for (auto& list : neighbors) {
        std::sort(list.begin(), list.end());
        list.erase(std::unique(list.begin(), list.end()), list.end());
    }
    // los vértices del borde de la malla final (agujeros sin rellenar) tampoco se mueven
    std::vector<char> frozen(quads.pinned);
    frozen.resize(n, 0);
    {
        std::map<std::pair<int, int>, int> uses;
        for (const auto& q : quads.quads)
            for (int k = 0; k < 4; ++k) ++uses[{std::min(q[k], q[(k + 1) % 4]), std::max(q[k], q[(k + 1) % 4])}];
        for (const auto& item : uses)
            if (item.second != 2) frozen[item.first.first] = frozen[item.first.second] = 1;
    }
    SurfaceGrid grid = surface_grid(mesh, 1.5 * edge_length);
    std::vector<Vec3> next(quads.points);
    for (int round = 0; round < RELAX_ROUNDS; ++round) {
        std::fill(normal.begin(), normal.end(), Vec3());
        for (const auto& q : quads.quads) {
            Vec3 nq = (quads.points[q[2]] - quads.points[q[0]]).cross(quads.points[q[3]] - quads.points[q[1]]);
            for (int v : q) normal[v] = normal[v] + nq;
        }
        for (size_t v = 0; v < n; ++v) {
            next[v] = quads.points[v];
            if (frozen[v] || neighbors[v].empty()) continue;
            Vec3 mean;
            for (int w : neighbors[v]) mean = mean + quads.points[w];
            mean = mean * (1.0 / neighbors[v].size());
            Vec3 nv = unit(normal[v]);
            Vec3 move = (mean - quads.points[v]) * 0.5;
            move = move - nv * move.dot(nv);
            Vec3 target = quads.points[v] + move, on;
            next[v] = project_to_surface(grid, target, on) ? on : target;
        }
        quads.points.swap(next);
    }
}

struct QuadReport {
    int vertices = 0, quads = 0, open_edges = 0, nonmanifold_edges = 0, poles = 0, degenerate = 0;
    int valence[9] = {0};  // 0..7, 8 = 8 o más (solo vértices interiores)
};

static QuadReport inspect_quads(const QuadMesh& quads) {
    QuadReport report;
    report.quads = static_cast<int>(quads.quads.size());
    std::map<std::pair<int, int>, int> uses;
    std::vector<int> valence(quads.points.size(), 0);
    std::vector<char> used(quads.points.size(), 0), on_border(quads.points.size(), 0);
    for (const auto& q : quads.quads) {
        std::set<int> distinct(q.begin(), q.end());
        if (distinct.size() < 4) ++report.degenerate;
        for (int k = 0; k < 4; ++k) {
            int a = q[k], b = q[(k + 1) % 4];
            used[a] = 1;
            ++uses[{std::min(a, b), std::max(a, b)}];
        }
    }
    for (const auto& item : uses) {
        ++valence[item.first.first];
        ++valence[item.first.second];
        if (item.second == 1) {
            ++report.open_edges;
            on_border[item.first.first] = on_border[item.first.second] = 1;
        } else if (item.second > 2) {
            ++report.nonmanifold_edges;
        }
    }
    for (size_t i = 0; i < quads.points.size(); ++i) {
        if (!used[i]) continue;
        ++report.vertices;
        if (on_border[i]) continue;
        report.valence[std::min(8, valence[i])]++;
        if (valence[i] != 4) ++report.poles;
    }
    return report;
}

static bool write_quads(const char* path, const QuadMesh& quads) {
    FILE* file = fopen(path, "w");
    if (!file) return false;
    for (const Vec3& p : quads.points) fprintf(file, "v %.6f %.6f %.6f\n", p.x, p.y, p.z);
    for (const auto& q : quads.quads) fprintf(file, "f %d %d %d %d\n", q[0] + 1, q[1] + 1, q[2] + 1, q[3] + 1);
    fclose(file);
    return true;
}

// ---------------------------------------------------------------- programa

static const int SPLIT_ROUNDS = 24;
static const double REFINE_RATIO = 2.0;  // aristas de entrada por arista final, como mínimo

static int run(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "uso: mbl_core entrada.obj salida.json [--field campo.bin] [--poles polos.txt] [--patches parches.txt] [--quads quads.obj] [--faces caras] [--memory-mb N] [--feature-angle grados]\n");
        return 2;
    }
    const char* field_path = nullptr;
    const char* poles_path = nullptr;
    const char* patches_path = nullptr;
    const char* quads_path = nullptr;
    size_t memory_mb = 0;
    double feature_degrees = 40.0;
    int target_faces = 0;
    for (int i = 3; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        if (name == "--field") field_path = argv[i + 1];
        else if (name == "--poles") poles_path = argv[i + 1];
        else if (name == "--patches") patches_path = argv[i + 1];
        else if (name == "--quads") quads_path = argv[i + 1];
        else if (name == "--memory-mb") memory_mb = static_cast<size_t>(atoll(argv[i + 1]));
        else if (name == "--feature-angle") feature_degrees = atof(argv[i + 1]);
        else if (name == "--faces") target_faces = atoi(argv[i + 1]);
    }
    limit_resources(memory_mb);
    auto started = std::chrono::steady_clock::now();
    Mesh mesh;
    if (!load_obj(argv[1], mesh)) {
        fprintf(stderr, "no se pudo leer la malla: %s\n", argv[1]);
        return 2;
    }
    const int input_triangles = static_cast<int>(mesh.tris.size());
    RefineReport refined;
    if (target_faces > 0) {
        // S2: aristas de como mucho un tercio de la arista final
        double area = 0;
        for (const auto& tri : mesh.tris)
            area += 0.5 * (mesh.points[tri[1]] - mesh.points[tri[0]]).cross(mesh.points[tri[2]] - mesh.points[tri[0]]).norm();
        refined = refine_mesh(mesh, std::sqrt(area / target_faces) / (getenv("MBL_REFINE_RATIO") ? atof(getenv("MBL_REFINE_RATIO")) : REFINE_RATIO));
    }
    build_topology(mesh);
    if (const char* dump = getenv("MBL_CORE_MESH")) {
        // depuración: la malla tras S2, para mirar el campo sobre ella
        if (FILE* file = fopen(dump, "w")) {
            for (const Vec3& q : mesh.points) fprintf(file, "v %.7f %.7f %.7f\n", q.x, q.y, q.z);
            for (const auto& tri : mesh.tris) fprintf(file, "f %d %d %d\n", tri[0] + 1, tri[1] + 1, tri[2] + 1);
            fclose(file);
        }
    }
    Analysis analysis = analyze(mesh, feature_degrees);
    int levels = 0;
    std::vector<Complex> z = solve_field(mesh, analysis, target_faces, levels);
    FieldReport field = inspect_field(mesh, analysis, z);
    double field_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

    // S4: líneas desde los polos y partición hasta que los parches sean discos de cuatro esquinas
    const double edge_length = target_faces > 0 ? std::sqrt(analysis.total_area / target_faces) : 4.0 * analysis.mean_edge;
    Tracer tracer;
    tracer.mesh = &mesh;
    tracer.graph = edge_graph(mesh);
    tracer.frames = vertex_frames(mesh, tracer.graph, z);
    const size_t n = mesh.points.size();
    tracer.traced.assign(tracer.graph.ends.size(), 0);
    tracer.stop.assign(n, 0);
    tracer.on_line.assign(n, 0);
    tracer.border.assign(n, 0);
    tracer.degree.assign(n, 0);
    tracer.head.assign(n, -1);
    tracer.fixed_edge.assign(tracer.graph.ends.size(), 0);
    for (size_t t = 0; t < mesh.tris.size(); ++t)
        for (int k = 0; k < 3; ++k)
            if (analysis.feature_side[t * 3 + k] || mesh.across[t][k] < 0) {
                int a = mesh.tris[t][k], b = mesh.tris[t][(k + 1) % 3];
                mark_edge(tracer, tracer.graph.side_edge[t * 3 + k]);
                tracer.fixed_edge[tracer.graph.side_edge[t * 3 + k]] = 1;
                tracer.on_line[a] = tracer.on_line[b] = 1;
                if (mesh.across[t][k] < 0) tracer.border[a] = tracer.border[b] = tracer.stop[a] = tracer.stop[b] = 1;
            }
    std::vector<int> pole_list;
    for (const auto& pole : field.poles) {
        tracer.pole_quarters[pole.first] = pole.second;
        tracer.stop[pole.first] = 1;
        pole_list.push_back(pole.first);
    }
    trace_from_poles(tracer, pole_list);
    // distancia para medir la forma de las líneas: que el zigzag por las aristas no cuente como giro
    const double radius = std::max(0.75 * edge_length, 3.0 * analysis.mean_edge);
    const int pole_lines = tracer.lines;
    Layout layout = build_layout(tracer, radius);
    const int first_patches = static_cast<int>(layout.patches.size()), first_good = layout.good;
    int rounds = 0;
    for (; rounds < SPLIT_ROUNDS && layout.good < static_cast<int>(layout.patches.size()); ++rounds) {
        int changes = merge_bad_patches(tracer, layout, edge_length);
        changes += split_bad_patches(tracer, layout);
        if (changes == 0) break;
        layout = build_layout(tracer, radius);
    }
    if (prune_dangling(tracer) > 0) layout = build_layout(tracer, radius);
    const int forced_patches = getenv("MBL_NO_FORCE") ? 0 : force_four_corners(tracer, layout);
    double layout_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

    // S5 y S6
    // las tiras que se colapsan dejan el resto más grande: si el total se aleja de lo pedido, se ajusta la arista y se
    // vuelve a repartir (dos veces como mucho)
    const Layout before_quantize = layout;
    double quad_edge = edge_length;
    QuantReport quant = quantize(layout, quad_edge);
    QuadMesh quads;
    fill_patches(tracer, layout, quads);
    for (int again = 0; again < 2 && target_faces > 0 && !quads.quads.empty(); ++again) {
        double ratio = static_cast<double>(quads.quads.size()) / target_faces;
        if (ratio > 0.92 && ratio < 1.08) break;
        quad_edge *= std::sqrt(ratio);
        layout = before_quantize;
        quant = quantize(layout, quad_edge);
        quads = QuadMesh();
        fill_patches(tracer, layout, quads);
    }
    if (!getenv("MBL_NO_RELAX")) relax_quads(mesh, quads, quad_edge);
    QuadReport quad_report = inspect_quads(quads);

    if (field_path && !write_field(field_path, mesh, z)) fprintf(stderr, "no se pudo escribir %s\n", field_path);
    if (poles_path) {
        // un polo por línea: x y z índice_en_cuartos
        if (FILE* file = fopen(poles_path, "w")) {
            for (const auto& pole : field.poles) {
                const Vec3& p = mesh.points[pole.first];
                fprintf(file, "%.6f %.6f %.6f %d\n", p.x, p.y, p.z, pole.second);
            }
            fclose(file);
        }
    }
    if (patches_path) {
        // parche de cada triángulo, uno por línea
        if (FILE* file = fopen(patches_path, "w")) {
            for (int value : layout.face_patch) fprintf(file, "%d\n", value);
            fclose(file);
        }
    }
    if (quads_path && !write_quads(quads_path, quads)) fprintf(stderr, "no se pudo escribir %s\n", quads_path);
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

    FILE* out = fopen(argv[2], "w");
    if (!out) return 2;
    int used = 0;
    {
        std::vector<char> seen(mesh.points.size(), 0);
        for (const auto& tri : mesh.tris) for (int v : tri) if (!seen[v]) { seen[v] = 1; ++used; }
    }
    fprintf(out, "{\n \"input_triangles\": %d, \"refine_passes\": %d, \"refine_splits\": %d, \"refine_flips\": %d,\n",
            input_triangles, refined.passes, refined.splits, refined.flips);
    fprintf(out, " \"vertices\": %d, \"triangles\": %d, \"edges\": %d, \"pieces\": %d,\n", used,
            static_cast<int>(mesh.tris.size()), mesh.edge_count, mesh.pieces);
    fprintf(out, " \"euler\": %d, \"border_edges\": %d, \"nonmanifold_edges\": %d, \"flipped_pairs\": %d,\n",
            used - mesh.edge_count + static_cast<int>(mesh.tris.size()), mesh.border_edges, mesh.nonmanifold_edges, mesh.flipped_pairs);
    fprintf(out, " \"diagonal\": %.6f, \"mean_edge\": %.6f, \"area\": %.6f, \"features\": %d,\n", analysis.diagonal,
            analysis.mean_edge, analysis.total_area, analysis.features);
    fprintf(out, " \"mirror\": [%.4f, %.4f, %.4f],\n", analysis.mirror[0], analysis.mirror[1], analysis.mirror[2]);
    fprintf(out, " \"levels\": %d, \"singular_total\": %d, \"singular_by_quarters\": {", levels, field.singular_total);
    bool first = true;
    for (int q = 0; q < 9; ++q)
        if (field.singular[q]) {
            fprintf(out, "%s\"%d\": %d", first ? "" : ", ", q - 4, field.singular[q]);
            first = false;
        }
    fprintf(out, "},\n \"index_sum\": %.4f, \"vertices_checked\": %d, \"vertices_skipped\": %d,\n", field.index_sum,
            field.vertices_checked, field.vertices_skipped);
    fprintf(out, " \"mean_turn_degrees\": %.4f, \"feature_error_degrees\": %.4f, \"hash\": \"%016llx\",\n",
            field.mean_turn_degrees, field.feature_error_degrees, static_cast<unsigned long long>(field.hash));
    int line_edges = 0;
    for (char c : tracer.traced) line_edges += c;
    fprintf(out, " \"lines\": %d, \"pole_lines\": %d, \"line_edges\": %d, \"segments\": %d, \"split_rounds\": %d,\n", tracer.lines,
            pole_lines, line_edges, static_cast<int>(layout.segments.size()), rounds);
    fprintf(out, " \"first_patches\": %d, \"first_good_patches\": %d, \"patches\": %d, \"good_patches\": %d, \"bad_reasons\": {",
            first_patches, first_good, static_cast<int>(layout.patches.size()), layout.good);
    first = true;
    for (const auto& item : layout.reasons) {
        fprintf(out, "%s\"%s\": %d", first ? "" : ", ", item.first.c_str(), item.second);
        first = false;
    }
    fprintf(out, "},\n \"quant_variables\": %d, \"quant_constraints\": %d, \"quant_violations\": %d, \"quant_solves\": %d, \"quant_error\": %.4f,\n",
            quant.variables, quant.constraints, quant.violations, quant.solves, quant.mean_error);
    fprintf(out, " \"collapsed_patches\": %d, \"dropped_quads\": %d, \"quant_repairs\": %d, \"forced_patches\": %d,\n", quads.patches_collapsed, quads.quads_dropped, quant.repairs, forced_patches);
    fprintf(out, " \"quads\": %d, \"quad_vertices\": %d, \"open_edges\": %d, \"quad_nonmanifold_edges\": %d, \"degenerate_quads\": %d, \"quad_poles\": %d, \"valence\": [",
            quad_report.quads, quad_report.vertices, quad_report.open_edges, quad_report.nonmanifold_edges, quad_report.degenerate, quad_report.poles);
    for (int k = 0; k < 9; ++k) fprintf(out, "%s%d", k ? ", " : "", quad_report.valence[k]);
    fprintf(out, "],\n \"field_seconds\": %.3f, \"layout_seconds\": %.3f,\n", field_seconds, layout_seconds);
    fprintf(out, " \"seconds\": %.3f, \"peak_memory_mb\": %.1f\n}\n", seconds, peak_memory_mb());
    fclose(out);
    return 0;
}

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::bad_alloc&) {
        fprintf(stderr, "sin memoria dentro del tope: sube --memory-mb o reduce la malla\n");
        return 3;
    }
}
