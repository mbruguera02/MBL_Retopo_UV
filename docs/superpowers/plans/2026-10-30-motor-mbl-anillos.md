# Motor MBL por anillos — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un motor de retopo cuyos anillos cierran por construcción, con quads parejos y sin defectos, mejor que polyRetopo en `quality.py`.

**Architecture:** Los anillos son curvas de nivel de la distancia geodésica desde una raíz. Entre anillos se tejen quads; donde el nº de puntos cambia o un anillo se parte se usan celdas verificadas en código. El resultado se pega al original con las reglas ya existentes y compite con QuadriFlow y Maya por calidad.

**Tech Stack:** Python 3 + numpy (sin scipy, sin Maya) en `mbl_engine/`, C++ solo si una pieza pasa de 5 s; pruebas con mayapy.

**Spec:** `docs/superpowers/specs/2026-10-30-motor-mbl-anillos-design.md`

## Global Constraints

- Núcleo `mbl_engine`: Python con numpy y C++, sin Maya y sin scipy; Maya solo como adaptador.
- Sin código GPL en el núcleo; se acreditan todos los autores en `CREDITS.md`.
- Plugin de Maya 2026 en Windows. Comentarios en español, nombres de código en inglés.
- Tiempo máximo por modelo: 5 minutos (300 s), cancelable con `tick`.
- Pruebas del núcleo: `"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe" tests\test_engine.py <parte_del_nombre>` (suite completa debe seguir en verde).
- Metas medibles (spec): aristas en loops cerrados > 70 %, quads deformados < 2 %, saltos de tamaño < 3 %, 0 caras dobladas o dadas la vuelta, 0 agujeros nuevos, simetría exacta con bucle central, error de forma ≤ Maya.
- Criterio de abandono (tareas 6-7): si los anillos cierran pero las transiciones dejan más defectos que Maya, se deja QuadriFlow como base y se avisa.

## Review Focus

1. Modelo con piezas sueltas (ojos, dientes): cada pieza lleva su propia raíz y no falla ni deja anillos cruzados.
2. Modelo con bordes abiertos (el águila tiene 3): los anillos que tocan un borde son tiras abiertas; no se crean agujeros ni se cierran a la fuerza.
3. Zonas más finas que un quad (plumas, dedos): un anillo con menos de 4 puntos se funde o se omite sin dejar triángulos.
4. Cancelar o pasar de 300 s: la función vuelve enseguida con el mejor resultado parcial o con un error claro, sin dejar procesos vivos.
5. Modelo no simétrico con simetría en "auto": no se refleja nada y no se pierde forma.

---

### Task 1: Celdas de transición verificadas

**Files:**
- Create: `mbl_engine/cells.py`
- Test: `tests/test_engine.py` (`test_transition_cells`)

**Interfaces:**
- Produces: `Patch` (dataclass: `points: np.ndarray` N×2, `faces: list[list[int]]`, `top: list[int]`, `bottom: list[int]`, vértices de borde de izquierda a derecha); `verify_patch(patch) -> dict` con claves `all_quads`, `manifold`, `charge_ok` (bool), `top_edges`, `bottom_edges` (int), `poles` (`{3: n, 5: n, 6: n}`); `transition_patch(n: int, m: int, sides: tuple[int, int] = (1, 1)) -> Patch`.

- [ ] **Step 1: Write the failing test.** Para cada `(n, m)` en `[(4, 2), (6, 2), (5, 1), (3, 1), (8, 4), (6, 4), (5, 3)]`: `v = verify_patch(transition_patch(n, m))`; assert `v["all_quads"] and v["manifold"] and v["charge_ok"]` y `(v["top_edges"], v["bottom_edges"]) == (n, m)`. Assert `transition_patch(2, 1)` lanza `ValueError` (con lados (1, 1), n + m impar no cierra con quads; el texto externo que propone 2:1 con un solo polo es incorrecto). Assert `transition_patch(2, 1, sides=(1, 2))` sí verifica.
- [ ] **Step 2: Run it** (`... test_engine.py transition_cells`). Expected: FAIL, módulo `cells` no existe.
- [ ] **Step 3: Implement** `cells.py`. `charge_ok` comprueba el invariante de disco de quads: suma sobre interiores de `(4 - valencia)` más suma sobre bordes de `(3 - valencia)` igual a 4. `transition_patch`: construir por búsqueda sobre una banda de quads (colapso de columnas con pares de polos 3-5); la búsqueda debe terminar para n ≤ 12 y devolver el parche con menos polos.
- [ ] **Step 4: Run test.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: celdas de transición verificadas (cells.py)`.

### Task 2: Plantillas de rama

**Files:**
- Modify: `mbl_engine/cells.py`
- Test: `tests/test_engine.py` (`test_branch_cells`)

**Interfaces:**
- Consumes: `Patch`, `verify_patch` (Task 1).
- Produces: `branch_patch(parent: int, children: tuple[int, ...]) -> Patch` (un anillo de `parent` aristas que se parte en anillos de `children` aristas; `bottom` queda vacío y `children_vertices: list[list[int]]` en el parche).

- [ ] **Step 1: Test.** `branch_patch(8, (4, 4))`, `branch_patch(10, (6, 4))`, `branch_patch(12, (4, 4, 4))`: todo quads, manifold, `charge_ok` generalizado (suma de `4 - v` en interiores igual a `4 * (1 - (nº de hijos - 1)) - Σ(3 - v)` de bordes: la característica de Euler de un disco con k agujeros); cada hijo con el nº de aristas pedido. `branch_patch(7, (4, 3))` (paridad impar) lanza `ValueError`.
- [ ] **Step 2: Run** (FAIL, falta `branch_patch`).
- [ ] **Step 3: Implement** reutilizando la búsqueda de la Task 1 con un agujero por hijo.
- [ ] **Step 4: Run** (PASS). **Step 5: Commit** `feat: plantillas de rama verificadas`.
- [ ] **Gate (fallo rápido):** las tareas 1 y 2 son lo único que el experimento no probó. Si alguna celda no sale con solo quads y balance correcto para los casos de las pruebas, se para aquí, se avisa al usuario y se rediseñan las celdas antes de escribir las tareas 3 a 10.

### Task 3: Anillos por distancia

**Files:**
- Create: `mbl_engine/rings.py`
- Test: `tests/test_engine.py` (`test_rings`)

**Interfaces:**
- Produces: `piece_roots(points, tris) -> list[int]` (un vértice por pieza conexa: el más cercano al centro de su caja); `geodesic_distance(points, tris, sources: list[int]) -> np.ndarray` (inf si no alcanzable); `Ring` (dataclass: `level: int`, `edges: list[tuple[int, int]]` ordenadas alrededor, `positions: np.ndarray` K×3, `closed: bool`); `level_rings(points, tris, distance, spacing: float) -> list[list[Ring]]`; `ring_links(levels) -> list[list[tuple[int, int]]]` (para cada nivel k, pares `(i, j)` anillo i del nivel k con anillo j del k+1).

- [ ] **Step 1: Test.** Esfera: cada nivel tiene 1 anillo cerrado y 0 ramas. Tubo en Y sintético (reutilizar/crear `y_tube()` en el test): hay al menos un nivel con enlace `1 → 2`. Dos esferas sueltas: `len(piece_roots) == 2` y ambos tienen anillos. Rejilla con borde abierto: los anillos que tocan el borde salen con `closed == False` (Review Focus 2). Cada anillo cerrado tiene sus aristas ordenadas formando un ciclo.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** con Dijkstra de `heapq` (como en el experimento) y extracción de curvas de nivel por aristas cruzadas ordenadas por recorrido; cambiar a C++ solo si el humano pasa de 5 s.
- [ ] **Step 4: Run** (PASS). **Step 5: Commit** `feat: anillos por distancia (rings.py)`.

### Task 4: Malla de tubo (anillos sin cambios de tamaño)

**Files:**
- Create: `mbl_engine/ringmesh.py`
- Test: `tests/test_engine.py` (`test_ring_tube`)

**Interfaces:**
- Consumes: `Ring`, `level_rings`, `ring_links`.
- Produces: `sample_ring(ring: Ring, count: int, phase: float = 0.0) -> np.ndarray` (count×3, reparto por longitud de arco desde el punto de `phase`); `tube_mesh(levels, counts: list[int]) -> tuple[np.ndarray, list[list[int]]]` (solo anillos con el mismo `count` entre niveles consecutivos).

- [ ] **Step 1: Test.** Cilindro cerrado de 40 aristas de largo: `tube_mesh` da solo quads, sin bordes salvo los extremos, `metrics.loop_stats` con `loop_closed_percent > 90`, `quality.report(...)["shape"]["bad_percent"] < 2`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** La fase de cada anillo se alinea con la del anterior (mínimo giro entre puntos correspondientes) para que las líneas a lo largo salgan paralelas.
- [ ] **Step 4: Run** (PASS). **Step 5: Commit** `feat: malla de tubo por anillos`.

### Task 5: Transiciones y ramas en la malla

> **Descomposición decidida tras las tareas 1-4 (hecha con los resultados de las celdas):** `transition_patch` y `branch_patch` solo existen para pocas aristas (borde de hasta 18 y 16), pero los anillos reales tienen de 40 a 160. Las celdas verificadas salen con pares 3-5 (4:2, 3:1, 6:4, 5:3: un 3 y un 5; 6:2, 5:1, 8:4: dos y dos) y las ramas con cuatro polos de 5, sin ningún 6. Para tamaños grandes se compone **una celda mínima más columnas rectas**. Subtareas, cada una con su prueba antes del código:
> - **5a. `annulus_faces(top: int, bottom: int) -> list[list[int]]`** (ids: arriba 0..top-1, abajo top..top+bottom-1, cíclico): `d = (top - bottom) / 2` celdas 4:2 repartidas por igual y columnas rectas en el resto; exige `top >= 4d` (razón <= 2:1) y paridad par; con `bottom > top` es la celda al revés. `plan_counts(m_from, m_to) -> list[int]` da los recuentos pares intermedios con razón <= 2. Prueba: solo quads, variedad cerrada, `poles` justos (d pares 3-5), para (12, 8), (40, 38), (24, 40).
> - **5b. `cap_faces(count)`**: 4 -> un quad; 6 -> centro y 3 quads; 8 -> centro y 4 quads; otro -> `ValueError` (se baja antes con `plan_counts`).
> - **5c. `insert_column(patch, u, v) -> Patch`** sobre una `branch_patch`: duplica una arista entre el anillo padre y uno hijo cuyos extremos tienen valencia 3 en el parche; los anillos crecen en 1 y la diferencia `n - a - b` no cambia. Con eso `branch_large(n, children)` para `n = a + b - 2`; el resto de la diferencia se absorbe con 5a antes de la rama. Prueba: (4,(3,3)) con 10 columnas sigue siendo quads, variedad y balance -4.
> - **5d. `mesh_from_rings(levels, links, target_edge)`**: recuentos pares por anillo, puntos con `sample_ring` y desfase alineado con el vecino, tubo con 5a, ramas y uniones con 5c, caps con 5b. Anillos abiertos: se omiten con aviso (Review Focus 2). Prueba: tubo cónico, toro y tubo en Y, como en el texto de abajo.


**Files:**
- Modify: `mbl_engine/ringmesh.py`
- Test: `tests/test_engine.py` (`test_ring_branches`)

**Interfaces:**
- Consumes: `transition_patch`, `branch_patch`, `tube_mesh`.
- Produces: `mesh_from_rings(levels, links, target_edge: float) -> tuple[np.ndarray, list[list[int]]]` (reparte `count = round(longitud / target_edge)` por anillo, corrige la paridad y pone celdas donde cambia el nº de puntos o el anillo se parte).

- [ ] **Step 1: Test.** Tubo cónico (de 40 a 24 aristas por vuelta) y tubo en Y: 100 % quads, sin agujeros, variedad cerrada, `quality.grade(...)[0] < 2`, `loop_closed_percent > 70`. Anillo de menos de 4 puntos (Review Focus 3): el resultado no tiene triángulos.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** (PASS). **Step 5: Commit** `feat: transiciones y ramas en la malla de anillos`.

### Task 6: Retopo por anillos completo

**Files:**
- Create: `mbl_engine/ringretopo.py`
- Test: `tests/test_engine.py` (`test_ring_retopo`)

**Interfaces:**
- Consumes: todo lo anterior; `fit.guarded_project`, `fit.fit_to_source`, `symmetry.cut_half`, `symmetry.mirror_mesh`, `metrics.SurfaceReference`.
- Produces: `ring_retopo(points, faces, target_faces: int, symmetric: bool | None = None, tick=None, log=None, time_budget: float = 300.0) -> tuple[np.ndarray, list[list[int]], dict]` (informe con `rings`, `closed_percent`, `branches`, `seconds`).

- [ ] **Step 1: Test.** (a) Esfera de 1.200 caras: solo quads, error de forma < 0,3 %. (b) Dos piezas sueltas (Review Focus 1). (c) Modelo no simétrico con `symmetric=None` no se refleja (Review Focus 5). (d) `tick` que lanza tras 3 llamadas: la excepción sale y no queda ningún hilo ni proceso vivo (Review Focus 4). (e) Con `symmetric=True` el reflejo es exacto (`symmetry.mirror_error < 0.01`) y hay aristas sobre el plano.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** Raíz por pieza, distancia, anillos, `mesh_from_rings`, proyección protegida, ajuste, simetría por mitad con el bucle central (como en `retopo.retopo`). Con `time_budget` agotado, devolver `EngineError` claro.
- [ ] **Step 4: Run** (PASS). **Step 5: Commit** `feat: retopo por anillos (ringretopo.py)`.

### Task 7: Evaluación contra Maya y puerta de abandono

**Files:**
- Create: `tests/bench_rings.py`
- Modify: `docs/superpowers/specs/2026-10-30-motor-mbl-anillos-design.md` (resultados al final)

**Interfaces:**
- Consumes: `ring_retopo`, `quality.report`, `quality.grade`, `quality.defect_score`.

- [ ] **Step 1: Write** `bench_rings.py`: para `human` y `eagle` (OBJ de `scratchpad/cases` o de `geo/`) y `goodretopo.obj` si existe, imprime una línea por modelo con caras, quads, polos, % de loops cerrados, deformados, saltos, defectos, simetría, `defect_score` y segundos, junto a la fila de Maya.
- [ ] **Step 2: Run** y pegar la tabla en la spec. **Gate:** si no se cumplen las metas de forma y defectos, parar y avisar al usuario antes de seguir (criterio de abandono).
- [ ] **Step 3: Commit** `docs: resultados del motor por anillos frente a Maya`.

### Task 8: Criatura y límite de tiempo

**Files:**
- Modify: `mbl_engine/ringretopo.py`, `mbl_engine/rings.py`
- Test: `tests/test_engine.py` (`test_ring_retopo_time_budget`)

- [ ] **Step 1: Test.** Con `time_budget=0.5` sobre una malla de 50.000 triángulos, `ring_retopo` lanza `EngineError` en menos de 3 s.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** comprobaciones de tiempo entre fases y, si la criatura (528.554 triángulos) pasa de 300 s, reducir la malla con `decimate.qem_decimate` antes de calcular anillos y pasar el cálculo de la distancia a C++ (`engine/mbl_geom`, orden `D` nueva).
- [ ] **Step 4: Run** y medir la criatura (≤ 300 s). **Step 5: Commit** `perf: anillos de la criatura dentro de 5 minutos`.

### Task 9: Motor en el plugin

**Files:**
- Modify: `mbl_retopo_uv/retopo.py` (`ENGINES["rings"]`, `_rings_engine`, candidato en `_auto_with_mbl`), `mbl_retopo_uv/ui.py`, `mbl_retopo_uv/config.py`
- Test: `tests/run_tests.py` (`test_rings_engine`)

- [ ] **Step 1: Test** (como `test_mbl_engine`): motor `rings` sobre `make_dense_mesh()`: una malla `_retopo`, solo quads, original oculto, enlace al original, deshacer de una vez. En Automático, el motor de anillos entra como candidato y gana solo si su `defect_score` es menor.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** Opción nueva "Motor MBL (anillos)" en la ventana y su texto; el aviso de tiempo estimado usa el tamaño de la malla.
- [ ] **Step 4: Run** la suite de Maya completa (verde). **Step 5: Commit** `feat: motor de anillos en el plugin`.

### Task 10: Documentación y créditos

**Files:**
- Modify: `README.md`, `CREDITS.md`, `PROMPT_MOTOR_MBL.md`

- [ ] **Step 1:** Explicar el método, la tabla de la Task 7 y lo pendiente; créditos de las referencias usadas (distancia geodésica: Dijkstra; transiciones: referencias de quads verificadas en código).
- [ ] **Step 2: Commit** `docs: motor por anillos`.
