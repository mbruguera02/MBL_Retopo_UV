# Motor nativo (`mbl_core`): plan de trabajo

Punto de partida para programar el motor desde cero. Rama `f1-mbl-core`. El motor actual y Maya siguen funcionando al lado hasta que este los supere en el banco.

## Decisiones ya tomadas

- Todo el núcleo es código nuestro en C++. Nada de programas externos en el plugin; lo abierto con licencia MIT/BSD se puede integrar con crédito. Nada GPL.
- Sin fuerza bruta: se analiza, se decide y se hace **una pasada**. Solo se rehace una zona que el juez marque, con un motivo medido.
- Proceso aparte de Maya: si falla, Maya no se cae.
- No invasivo: prioridad baja, tope de memoria del sistema, cancelable, hilos limitados.
- Determinista: la misma malla da el mismo resultado.

## Pendiente de confirmar

**Vía de S4-S6: se propone por parches** (trazar el campo, dividir la superficie en parches de cuatro lados, repartir enteros, rellenar con rejillas).

| | Por parches (propuesta) | Red global (tipo QuadCover + QEx) |
|---|---|---|
| Anillos en tubos | Cierran por construcción | Hay que forzarlo con holonomía |
| Rehacer una zona | Natural: un parche | Difícil: el sistema es global |
| UVs | Cada parche ya es una rejilla plana | Hay que derivarlas |
| Memoria | Baja | Sistema lineal grande |
| Punto débil | Trazar sobre mallas sucias o muy finas | Pliegues y extracción de quads |

Si el trazado no es robusto en criatura y midnight al acabar la fase 3, se pasa a red global: S0-S3 y S7-S8 valen igual.

## Etapas y contratos

Cada etapa lee y escribe datos definidos, se prueba por separado y tiene sus cifras.

| Etapa | Entrada → salida | Comprobación |
|---|---|---|
| S0 Entrada | Malla → triángulos limpios, reducidos a la escala del objetivo | Forma conservada, piezas, no-manifold |
| S1 Análisis | → cantos, tubos, grosor, simetría, visibilidad | Frente a lo marcado a mano en el banco |
| S2 Tamaño | → tamaño de cara por zona | Caras previstas frente a pedidas |
| S3 Campo | → cruces y polos | Poincaré-Hopf, polos, estabilidad |
| S4 Parches | → grafo de parches de cuatro lados | Sin parches degenerados, anillos cerrados |
| S5 Enteros | → nº de divisiones por lado | Caras frente a objetivo; todo casa |
| S6 Quads | → malla de quads | 100 % quads, sin agujeros ni caras volteadas |
| S7 Ajuste | → pegada al original, relajada, zonas rehechas | Error de forma, fealdad |
| S8 Juez | → nota; si pierde contra Maya, se usa Maya | `mbl_engine/quality.py` |
| U1-U5 UVs | Parches → islas, aplanado, empaquetado | `mbl_engine/uv_quality.py` |

## Estado de partida

Hecho en `engine/mbl_core/core.cpp`: S0 sin reducción, S1 (cantos, bordes, simetría) y S3 (campo por niveles con cantos y curvatura). `tests/test_core.py`: 9 pruebas sin Maya.

Medido en humano (29.790 triángulos): 0,12 s y 19 MB. Simetría en X detectada (99,95 %).

**Problema abierto: demasiados polos en el campo.** Referencia del banco a unas 5.000 caras: Maya 174, Motor MBL 166.

| Malla de entrada al campo | Polos |
|---|---|
| Humano original, ángulo de canto 40° | 542 |
| Humano original, ángulo de canto 100° | 479 |
| Reducido a 20.000 triángulos, 80° | 333 |
| Reducido a 10.000 triángulos, 80° | 287 |
| Reducido a 5.000 triángulos, 80° | 230 |

Conclusiones: los cantos no son la causa principal, y reducir ayuda pero no basta. Falta probar: suavizar la geometría que ve el campo, eliminar pares de polos +/- cercanos, y medir aparte las piezas pequeñas (ojos, dientes: el humano tiene 16). Los polos del campo no son aún los de la malla final: la cifra que decide es la de S6.

## Fases

Cada fase acaba en algo que se puede medir. **Primero una vertical simple de punta a punta** y luego se mejora cada etapa: así hay quads cuanto antes y el riesgo de S4 se ve pronto.

1. **Reducción nativa y polos.** Integrar `engine/mbl_decimate` en el núcleo; bajar los polos. *Listo si:* humano a 5.000 caras con ≤ 250 polos en el campo, en menos de 1 s.
2. **Vertical sobre mallas fáciles.** S4-S6 mínimos con enteros simples. *Listo si:* cubo, esfera y toro salen 100 % quads, sin agujeros, y el toro con todos sus anillos cerrados.
3. **Modelos reales.** Trazado robusto y enteros globales. *Listo si:* los 5 modelos del banco salen sin agujeros ni caras volteadas. Aquí se decide si la vía de parches se queda.
4. **Calidad.** S2 por zonas, S7 y S8. *Listo si:* fealdad ≤ Maya y anillos cerrados ≥ Maya en humano y criatura, a ≤ 2 veces el tiempo de Maya.
5. **Entrar en el plugin.** Adaptador en Python, opción experimental en la ventana, respaldo a Maya. *Listo si:* suites en verde.
6. **UVs desde los parches.** *Listo si:* continuidad en los cortes +10 puntos y aprovechamiento ≥ 60 %.
7. **Entradas completas y motor único:** guías, materiales, pincel, zonas sin polos.

Objetivo de recursos: a 25.000 caras, ≤ 2 GB y ≤ 90 s en un PC de 16 GB.

## Cómo trabajar

Compilar y probar, sin Maya y sin cargar el PC:

```
cmake -S engine/mbl_core -B engine/build-core -G "Visual Studio 17 2022" -A x64 -Wno-dev
cmake --build engine/build-core --config Release --parallel 1
python tests/test_core.py
```

- El banco (`tests/bench.py`) y las suites con Maya son pesados: solo con el PC libre y una medición cada vez.
- Mallas de prueba: `tests/test_core.py` las genera; las del banco están en `tests/bench_models.json` (no va en el repositorio).
- Cuidado: `mbl_decimate.exe` no entiende caras `v/vt/vn`; dale un OBJ solo con `f a b c`.
- Crédito en `CREDITS.md` y cabecera de archivo para todo lo que se tome de un artículo o de código abierto.
