// Decimado por cuádricas dentro de mbl_core (S0): el mismo código que engine/mbl_decimate/decimate.cpp, como función.
// Garland y Heckbert, "Surface Simplification Using Quadric Error Metrics", SIGGRAPH 1997. Código propio de MBL.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <array>
#include <cstring>
#include <queue>
#include <string>
#include <vector>

namespace qem {
// mbl_decimate: reduce una malla de triángulos por colapso de aristas con cuádricas.
//
// Garland y Heckbert, "Surface Simplification Using Quadric Error Metrics", SIGGRAPH 1997.
// Código propio de MBL (sin dependencias).
//
//   mbl_decimate entrada.obj salida.obj caras_objetivo [peso_borde]
//
// Conserva la topología: un colapso solo se hace si cumple la condición de enlace (no deja aristas
// con más de dos caras ni pajaritas), no da la vuelta a ninguna cara y no mueve los bordes abiertos
// (llevan cuádricas extra que los sujetan). Por eso nunca abre agujeros, a diferencia de agrupar
// vértices en una rejilla, que fundía las plumas finas del águila de prueba.


struct Vec { double x, y, z; };
static Vec operator+(Vec a, Vec b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
static Vec operator-(Vec a, Vec b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
static Vec operator*(Vec a, double s) { return {a.x * s, a.y * s, a.z * s}; }
static double dot(Vec a, Vec b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static Vec cross(Vec a, Vec b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
static double length(Vec a) { return std::sqrt(dot(a, a)); }

// matriz simétrica 4x4: a b c d / b e f g / c f h i / d g i j
struct Quadric {
    double q[10];
    Quadric() { for (double &v : q) v = 0; }
    void add(const Quadric &o) { for (int i = 0; i < 10; ++i) q[i] += o.q[i]; }
    // plano (n, d) con peso w
    void addPlane(Vec n, double d, double w) {
        q[0] += w * n.x * n.x; q[1] += w * n.x * n.y; q[2] += w * n.x * n.z; q[3] += w * n.x * d;
        q[4] += w * n.y * n.y; q[5] += w * n.y * n.z; q[6] += w * n.y * d;
        q[7] += w * n.z * n.z; q[8] += w * n.z * d; q[9] += w * d * d;
    }
    double error(Vec p) const {
        return q[0] * p.x * p.x + 2 * q[1] * p.x * p.y + 2 * q[2] * p.x * p.z + 2 * q[3] * p.x
             + q[4] * p.y * p.y + 2 * q[5] * p.y * p.z + 2 * q[6] * p.y
             + q[7] * p.z * p.z + 2 * q[8] * p.z + q[9];
    }
};

struct Candidate {
    double cost;
    int a, b;
    unsigned sa, sb;
    Vec position;
    bool operator<(const Candidate &o) const { return cost > o.cost; }  // montículo de mínimos
};

static std::vector<Vec> P;
static std::vector<std::array<int, 3>> F;
static std::vector<char> faceAlive, vertexAlive;
static std::vector<std::vector<int>> vertexFaces;
static std::vector<Quadric> Q;
static std::vector<unsigned> stamp;

static bool hasVertex(const std::array<int, 3> &f, int v) { return f[0] == v || f[1] == v || f[2] == v; }

static void aliveFaces(int v, std::vector<int> &out) {
    out.clear();
    for (int f : vertexFaces[v]) if (faceAlive[f]) out.push_back(f);
}

static void neighbours(int v, std::vector<int> &out) {
    out.clear();
    for (int f : vertexFaces[v]) {
        if (!faceAlive[f]) continue;
        for (int k = 0; k < 3; ++k) if (F[f][k] != v) out.push_back(F[f][k]);
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
}

// posición óptima del colapso a-b: la que minimiza el error, o la mejor de los extremos y el punto medio
static Vec bestPosition(int a, int b, double *cost) {
    Quadric s = Q[a];
    s.add(Q[b]);
    Vec best = P[a];
    double bestCost = s.error(P[a]);
    Vec candidates[2] = {P[b], (P[a] + P[b]) * 0.5};
    for (Vec c : candidates) {
        double e = s.error(c);
        if (e < bestCost) { bestCost = e; best = c; }
    }
    // resolver [a b c; b e f; c f h] x = -[d g i]
    const double *q = s.q;
    double det = q[0] * (q[4] * q[7] - q[5] * q[5]) - q[1] * (q[1] * q[7] - q[5] * q[2]) + q[2] * (q[1] * q[5] - q[4] * q[2]);
    double scale = std::fabs(q[0]) + std::fabs(q[4]) + std::fabs(q[7]);
    if (std::fabs(det) > 1e-12 * scale * scale * scale && scale > 0) {
        double rx = -q[3], ry = -q[6], rz = -q[8];
        double dx = rx * (q[4] * q[7] - q[5] * q[5]) - q[1] * (ry * q[7] - q[5] * rz) + q[2] * (ry * q[5] - q[4] * rz);
        double dy = q[0] * (ry * q[7] - q[5] * rz) - rx * (q[1] * q[7] - q[5] * q[2]) + q[2] * (q[1] * rz - ry * q[2]);
        double dz = q[0] * (q[4] * rz - ry * q[5]) - q[1] * (q[1] * rz - ry * q[2]) + rx * (q[1] * q[5] - q[4] * q[2]);
        Vec opt = {dx / det, dy / det, dz / det};
        // un óptimo lejos de la arista es un error numérico
        if (length(opt - (P[a] + P[b]) * 0.5) <= 2.0 * length(P[a] - P[b])) {
            double e = s.error(opt);
            if (e <= bestCost) { bestCost = e; best = opt; }
        }
    }
    *cost = bestCost < 0 ? 0 : bestCost;
    return best;
}

static void push(std::priority_queue<Candidate> &heap, int a, int b) {
    double cost;
    Vec pos = bestPosition(a, b, &cost);
    heap.push({cost, a, b, stamp[a], stamp[b], pos});
}

// ¿se puede colapsar a-b llevándolo a `position`?
static bool canCollapse(int a, int b, Vec position, const std::vector<char> &boundaryVertex,
                        const std::vector<char> &isBoundaryEdgeFlag) {
    std::vector<int> fa, fb, na, nb;
    aliveFaces(a, fa);
    aliveFaces(b, fb);
    std::vector<int> shared;
    for (int f : fa) if (hasVertex(F[f], b)) shared.push_back(f);
    if (shared.empty() || shared.size() > 2) return false;
    // un borde abierto solo se colapsa por su propia arista, y no entre dos vértices de borde por dentro
    if (boundaryVertex[a] && boundaryVertex[b] && shared.size() == 2) return false;
    (void)isBoundaryEdgeFlag;
    // condición de enlace: los vecinos comunes son justo los terceros vértices de las caras compartidas
    neighbours(a, na);
    neighbours(b, nb);
    std::vector<int> common;
    for (int v : na) if (std::binary_search(nb.begin(), nb.end(), v)) common.push_back(v);
    std::vector<int> thirds;
    for (int f : shared) for (int k = 0; k < 3; ++k) if (F[f][k] != a && F[f][k] != b) thirds.push_back(F[f][k]);
    std::sort(thirds.begin(), thirds.end());
    if (common != thirds) return false;
    // ninguna cara se da la vuelta ni se aplasta
    for (int side = 0; side < 2; ++side) {
        int keep = side == 0 ? a : b, gone = side == 0 ? b : a;
        const std::vector<int> &faces = side == 0 ? fa : fb;
        (void)gone;
        for (int f : faces) {
            if (hasVertex(F[f], side == 0 ? b : a)) continue;
            Vec p[3];
            for (int k = 0; k < 3; ++k) p[k] = F[f][k] == keep ? P[keep] : P[F[f][k]];
            Vec before = cross(p[1] - p[0], p[2] - p[0]);
            for (int k = 0; k < 3; ++k) p[k] = F[f][k] == keep ? position : P[F[f][k]];
            Vec after = cross(p[1] - p[0], p[2] - p[0]);
            double lb = length(before), la = length(after);
            if (lb < 1e-30) continue;
            if (la < 1e-30 || dot(before, after) / (lb * la) < 0.2) return false;
        }
    }
    return true;
}


// reduce (points, tris) a unos `target` triángulos sin cambiar la topología; devuelve los triángulos que quedan
inline long decimate_mesh(std::vector<std::array<double, 3>>& points, std::vector<std::array<int, 3>>& tris, long target,
                          double boundaryWeight = 100.0) {
    P.clear();
    F.clear();
    for (const auto& p : points) P.push_back({p[0], p[1], p[2]});
    for (const auto& t : tris) F.push_back(t);
    const int nv = (int)P.size(), nf = (int)F.size();
    faceAlive.assign(nf, 1);
    vertexAlive.assign(nv, 1);
    vertexFaces.assign(nv, {});
    Q.assign(nv, Quadric());
    stamp.assign(nv, 0);
    for (int f = 0; f < nf; ++f) for (int k = 0; k < 3; ++k) vertexFaces[F[f][k]].push_back(f);

    // cuádricas de los planos de las caras (ponderadas por área)
    for (int f = 0; f < nf; ++f) {
        Vec n = cross(P[F[f][1]] - P[F[f][0]], P[F[f][2]] - P[F[f][0]]);
        double len = length(n);
        if (len < 1e-30) continue;
        n = n * (1.0 / len);
        double d = -dot(n, P[F[f][0]]);
        for (int k = 0; k < 3; ++k) Q[F[f][k]].addPlane(n, d, 0.5 * len / 3.0);
    }
    // bordes abiertos: aristas con una sola cara. Un plano perpendicular a la cara por la arista sujeta el borde
    std::vector<std::pair<long long, int>> edgeKeys;
    edgeKeys.reserve((size_t)nf * 3);
    for (int f = 0; f < nf; ++f)
        for (int k = 0; k < 3; ++k) {
            int a = F[f][k], b = F[f][(k + 1) % 3];
            long long lo = std::min(a, b), hi = std::max(a, b);
            edgeKeys.push_back({lo * nv + hi, f});
        }
    std::sort(edgeKeys.begin(), edgeKeys.end());
    std::vector<char> boundaryVertex(nv, 0);
    std::vector<std::pair<int, int>> edges;
    for (size_t i = 0; i < edgeKeys.size();) {
        size_t j = i;
        while (j < edgeKeys.size() && edgeKeys[j].first == edgeKeys[i].first) ++j;
        int a = (int)(edgeKeys[i].first / nv), b = (int)(edgeKeys[i].first % nv);
        edges.push_back({a, b});
        if (j - i == 1) {
            int f = edgeKeys[i].second;
            Vec n = cross(P[F[f][1]] - P[F[f][0]], P[F[f][2]] - P[F[f][0]]);
            double ln = length(n);
            Vec e = P[b] - P[a];
            double le = length(e);
            if (ln > 1e-30 && le > 1e-30) {
                n = n * (1.0 / ln);
                Vec side = cross(e * (1.0 / le), n);
                double d = -dot(side, P[a]);
                Q[a].addPlane(side, d, boundaryWeight * le * le);
                Q[b].addPlane(side, d, boundaryWeight * le * le);
            }
            boundaryVertex[a] = boundaryVertex[b] = 1;
        }
        i = j;
    }

    std::priority_queue<Candidate> heap;
    for (auto &e : edges) push(heap, e.first, e.second);
    long alive = nf;
    std::vector<char> unused;
    std::vector<int> fa, fb, around;
    while (alive > target && !heap.empty()) {
        Candidate c = heap.top();
        heap.pop();
        int a = c.a, b = c.b;
        if (!vertexAlive[a] || !vertexAlive[b] || stamp[a] != c.sa || stamp[b] != c.sb) continue;
        if (!canCollapse(a, b, c.position, boundaryVertex, unused)) continue;
        // colapsar b en a
        aliveFaces(a, fa);
        for (int f : fa) if (hasVertex(F[f], b)) { faceAlive[f] = 0; --alive; }
        aliveFaces(b, fb);
        for (int f : fb) {
            for (int k = 0; k < 3; ++k) if (F[f][k] == b) F[f][k] = a;
            vertexFaces[a].push_back(f);
        }
        vertexFaces[b].clear();
        vertexAlive[b] = 0;
        P[a] = c.position;
        Q[a].add(Q[b]);
        if (boundaryVertex[b]) boundaryVertex[a] = 1;
        ++stamp[a];
        neighbours(a, around);
        for (int n : around) push(heap, a, n);
    }

    std::vector<int> remap(nv, -1);
    int count = 0;
    for (int f = 0; f < nf; ++f) {
        if (!faceAlive[f]) continue;
        for (int k = 0; k < 3; ++k) if (remap[F[f][k]] < 0) remap[F[f][k]] = count++;
    }
    points.assign(count, {{0, 0, 0}});
    for (int v = 0; v < nv; ++v) if (remap[v] >= 0) points[remap[v]] = {{P[v].x, P[v].y, P[v].z}};
    tris.clear();
    for (int f = 0; f < nf; ++f)
        if (faceAlive[f]) tris.push_back({{remap[F[f][0]], remap[F[f][1]], remap[F[f][2]]}});
    // liberar la memoria de trabajo
    std::vector<Vec>().swap(P);
    std::vector<std::array<int, 3>>().swap(F);
    std::vector<char>().swap(faceAlive);
    std::vector<char>().swap(vertexAlive);
    std::vector<std::vector<int>>().swap(vertexFaces);
    std::vector<Quadric>().swap(Q);
    std::vector<unsigned>().swap(stamp);
    return alive;
}

}  // namespace qem
