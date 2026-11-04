// mbl_core: núcleo nativo del Motor MBL. Por ahora, etapas S0 (entrada), S1 (análisis) y S3 (campo de direcciones).
//
//   mbl_core entrada.obj salida.json [--field campo.bin] [--memory-mb N] [--feature-angle grados]
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
    std::vector<LooseEdge> edges;
    edges.reserve(fine.target.size());
    for (int i = 0; i < fine.count; ++i) {
        coarse.constraint[fine.parent[i]] += fine.constraint[i] * fine.to_parent[i];
        coarse.align[fine.parent[i]] += fine.align[i] * fine.to_parent[i];
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
                sum += level.align[i];
            }
            double length = std::abs(sum);
            if (length > 1e-14) z[i] = sum / length;
        }
    }
}

static std::vector<Complex> solve_field(const Mesh& mesh, const Analysis& analysis, int& levels_used) {
    std::vector<Level> levels;
    levels.push_back(finest_level(mesh, analysis));
    while (levels.back().count > 64 && levels.size() < 24) {
        Level next = coarsen(levels.back());
        if (next.count >= levels.back().count) break;
        levels.push_back(std::move(next));
    }
    levels_used = static_cast<int>(levels.size());
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

// ---------------------------------------------------------------- programa

static int run(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "uso: mbl_core entrada.obj salida.json [--field campo.bin] [--memory-mb N] [--feature-angle grados]\n");
        return 2;
    }
    const char* field_path = nullptr;
    size_t memory_mb = 0;
    double feature_degrees = 40.0;
    for (int i = 3; i + 1 < argc; i += 2) {
        std::string name = argv[i];
        if (name == "--field") field_path = argv[i + 1];
        else if (name == "--memory-mb") memory_mb = static_cast<size_t>(atoll(argv[i + 1]));
        else if (name == "--feature-angle") feature_degrees = atof(argv[i + 1]);
    }
    limit_resources(memory_mb);
    auto started = std::chrono::steady_clock::now();
    Mesh mesh;
    if (!load_obj(argv[1], mesh)) {
        fprintf(stderr, "no se pudo leer la malla: %s\n", argv[1]);
        return 2;
    }
    build_topology(mesh);
    Analysis analysis = analyze(mesh, feature_degrees);
    int levels = 0;
    std::vector<Complex> z = solve_field(mesh, analysis, levels);
    FieldReport field = inspect_field(mesh, analysis, z);
    if (field_path && !write_field(field_path, mesh, z)) fprintf(stderr, "no se pudo escribir %s\n", field_path);
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

    FILE* out = fopen(argv[2], "w");
    if (!out) return 2;
    int used = 0;
    {
        std::vector<char> seen(mesh.points.size(), 0);
        for (const auto& tri : mesh.tris) for (int v : tri) if (!seen[v]) { seen[v] = 1; ++used; }
    }
    fprintf(out, "{\n \"vertices\": %d, \"triangles\": %d, \"edges\": %d, \"pieces\": %d,\n", used,
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
