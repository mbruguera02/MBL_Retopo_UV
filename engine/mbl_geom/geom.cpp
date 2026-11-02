// mbl_geom: servidor de punto mas cercano sobre una malla de triangulos (Motor MBL).
//
// El nucleo en Python busca el punto mas cercano por rejilla y numpy: unos 10.000 puntos por segundo.
// Esto lo hace con una jerarquia de cajas (BVH) y varios hilos: del orden de un millon por segundo.
// Es exacto (mismo resultado que mirar todos los triangulos).
//
// Protocolo por stdin / stdout, en binario, little endian:
//   'M' int32 nV, int32 nT, double[nV*3] puntos, int32[nT*3] triangulos   -> 1 byte (1 = bien)
//   'Q' int32 nQ, double[nQ*3] consultas -> double[nQ*3] puntos, double[nQ] distancias, int32[nQ] triangulo
//   'X' (o fin de entrada) termina.
// Una malla por proceso: una nueva 'M' sustituye a la anterior.
//
// Punto mas cercano de un punto a un triangulo: Christer Ericson, "Real-Time Collision Detection" (2004).
// Sin dependencias.

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

struct V3 {
    double x, y, z;
};
static inline V3 operator+(V3 a, V3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
static inline V3 operator-(V3 a, V3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
static inline V3 operator*(V3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
static inline double dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }

// punto mas cercano de p al triangulo abc (Ericson)
static V3 closestOnTriangle(V3 p, V3 a, V3 b, V3 c) {
    V3 ab = b - a, ac = c - a, ap = p - a;
    double d1 = dot(ab, ap), d2 = dot(ac, ap);
    if (d1 <= 0 && d2 <= 0) return a;
    V3 bp = p - b;
    double d3 = dot(ab, bp), d4 = dot(ac, bp);
    if (d3 >= 0 && d4 <= d3) return b;
    double vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0) {
        double v = d1 / (d1 - d3);
        return a + ab * v;
    }
    V3 cp = p - c;
    double d5 = dot(ab, cp), d6 = dot(ac, cp);
    if (d6 >= 0 && d5 <= d6) return c;
    double vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0) {
        double w = d2 / (d2 - d6);
        return a + ac * w;
    }
    double va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
        double w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return b + (c - b) * w;
    }
    double denom = 1.0 / (va + vb + vc);
    double v = vb * denom, w = vc * denom;
    return a + ab * v + ac * w;
}

struct Node {
    double lo[3], hi[3];
    int left, right;   // hijos; -1 en una hoja
    int begin, end;    // rango de triangulos (hoja)
};

static std::vector<V3> P;
static std::vector<int32_t> T;       // 3 por triangulo, ordenados por la jerarquia
static std::vector<int32_t> original; // indice original de cada triangulo ordenado
static std::vector<Node> nodes;
static const int LEAF = 4;

static int build(std::vector<int>& order, int begin, int end, const std::vector<V3>& centers) {
    Node node;
    for (int k = 0; k < 3; ++k) {
        node.lo[k] = 1e300;
        node.hi[k] = -1e300;
    }
    for (int i = begin; i < end; ++i) {
        for (int c = 0; c < 3; ++c) {
            const V3& v = P[T[3 * order[i] + c]];
            const double q[3] = {v.x, v.y, v.z};
            for (int k = 0; k < 3; ++k) {
                node.lo[k] = std::min(node.lo[k], q[k]);
                node.hi[k] = std::max(node.hi[k], q[k]);
            }
        }
    }
    node.left = node.right = -1;
    node.begin = begin;
    node.end = end;
    int index = (int)nodes.size();
    nodes.push_back(node);
    if (end - begin <= LEAF) return index;
    int axis = 0;
    double best = node.hi[0] - node.lo[0];
    for (int k = 1; k < 3; ++k)
        if (node.hi[k] - node.lo[k] > best) {
            best = node.hi[k] - node.lo[k];
            axis = k;
        }
    int mid = (begin + end) / 2;
    std::nth_element(order.begin() + begin, order.begin() + mid, order.begin() + end, [&](int a, int b) {
        const V3& ca = centers[a];
        const V3& cb = centers[b];
        const double va = axis == 0 ? ca.x : axis == 1 ? ca.y : ca.z;
        const double vb = axis == 0 ? cb.x : axis == 1 ? cb.y : cb.z;
        return va < vb;
    });
    int l = build(order, begin, mid, centers);
    int r = build(order, mid, end, centers);
    nodes[index].left = l;
    nodes[index].right = r;
    return index;
}

static inline double boxDistance2(const Node& n, V3 p) {
    double d = 0;
    const double q[3] = {p.x, p.y, p.z};
    for (int k = 0; k < 3; ++k) {
        double t = 0;
        if (q[k] < n.lo[k]) t = n.lo[k] - q[k];
        else if (q[k] > n.hi[k]) t = q[k] - n.hi[k];
        d += t * t;
    }
    return d;
}

static void closest(V3 p, V3& out, double& dist2, int32_t& owner) {
    dist2 = 1e300;
    owner = -1;
    out = p;
    int stack[128];
    int top = 0;
    stack[top++] = 0;
    while (top) {
        const Node& n = nodes[stack[--top]];
        if (boxDistance2(n, p) >= dist2) continue;
        if (n.left < 0) {
            for (int i = n.begin; i < n.end; ++i) {
                V3 c = closestOnTriangle(p, P[T[3 * i]], P[T[3 * i + 1]], P[T[3 * i + 2]]);
                V3 d = c - p;
                double d2 = dot(d, d);
                if (d2 < dist2 || (d2 == dist2 && original[i] < owner)) {
                    dist2 = d2;
                    out = c;
                    owner = original[i];
                }
            }
        } else {
            double dl = boxDistance2(nodes[n.left], p), dr = boxDistance2(nodes[n.right], p);
            // el mas cercano se visita primero (va el ultimo a la pila)
            if (dl < dr) {
                stack[top++] = n.right;
                stack[top++] = n.left;
            } else {
                stack[top++] = n.left;
                stack[top++] = n.right;
            }
        }
    }
}

static bool readExact(void* buffer, size_t bytes) {
    char* p = (char*)buffer;
    while (bytes) {
        size_t got = fread(p, 1, bytes, stdin);
        if (!got) return false;
        p += got;
        bytes -= got;
    }
    return true;
}

static void loadMesh() {
    int32_t nV, nT;
    if (!readExact(&nV, 4) || !readExact(&nT, 4)) exit(1);
    std::vector<double> raw((size_t)nV * 3);
    std::vector<int32_t> tris((size_t)nT * 3);
    if (nV && !readExact(raw.data(), raw.size() * 8)) exit(1);
    if (nT && !readExact(tris.data(), tris.size() * 4)) exit(1);
    P.resize(nV);
    for (int i = 0; i < nV; ++i) P[i] = {raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]};
    T = tris;
    std::vector<V3> centers(nT);
    std::vector<int> order(nT);
    for (int i = 0; i < nT; ++i) {
        order[i] = i;
        centers[i] = (P[T[3 * i]] + P[T[3 * i + 1]] + P[T[3 * i + 2]]) * (1.0 / 3.0);
    }
    nodes.clear();
    nodes.reserve(2 * (size_t)nT / LEAF + 8);
    if (nT) build(order, 0, nT, centers);
    std::vector<int32_t> sorted((size_t)nT * 3);
    original.assign(nT, 0);
    for (int i = 0; i < nT; ++i) {
        original[i] = order[i];
        for (int c = 0; c < 3; ++c) sorted[3 * i + c] = T[3 * order[i] + c];
    }
    T = sorted;
    fputc(1, stdout);
    fflush(stdout);
}

static void query() {
    int32_t nQ;
    if (!readExact(&nQ, 4)) exit(1);
    std::vector<double> q((size_t)nQ * 3);
    if (nQ && !readExact(q.data(), q.size() * 8)) exit(1);
    std::vector<double> out((size_t)nQ * 3), dist(nQ);
    std::vector<int32_t> owner(nQ);
    unsigned workers = std::max(1u, std::min(8u, std::thread::hardware_concurrency() / 2));
    if (nQ < 4000 || nodes.empty()) workers = 1;
    std::atomic<int> next(0);
    const int chunk = 512;
    auto work = [&]() {
        for (;;) {
            int begin = next.fetch_add(chunk);
            if (begin >= nQ) break;
            int end = std::min(nQ, begin + chunk);
            for (int i = begin; i < end; ++i) {
                V3 c;
                double d2;
                int32_t o = -1;
                if (nodes.empty()) {
                    c = {q[3 * i], q[3 * i + 1], q[3 * i + 2]};
                    d2 = 1e300;
                } else {
                    closest({q[3 * i], q[3 * i + 1], q[3 * i + 2]}, c, d2, o);
                }
                out[3 * i] = c.x;
                out[3 * i + 1] = c.y;
                out[3 * i + 2] = c.z;
                dist[i] = std::sqrt(d2);
                owner[i] = o;
            }
        }
    };
    std::vector<std::thread> threads;
    for (unsigned t = 1; t < workers; ++t) threads.emplace_back(work);
    work();
    for (auto& t : threads) t.join();
    fwrite(out.data(), 8, out.size(), stdout);
    fwrite(dist.data(), 8, dist.size(), stdout);
    fwrite(owner.data(), 4, owner.size(), stdout);
    fflush(stdout);
}

int main() {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    for (;;) {
        int c = fgetc(stdin);
        if (c == EOF || c == 'X') break;
        if (c == 'M') loadMesh();
        else if (c == 'Q') query();
    }
    return 0;
}
