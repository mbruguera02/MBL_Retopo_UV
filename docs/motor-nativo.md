# Motor nativo (`mbl_core`): plan de trabajo

Punto de partida para programar el motor desde cero. Rama `f1-mbl-core`. El motor actual y Maya siguen funcionando al lado hasta que este los supere en el banco.

## Requisitos: lo que hace bueno al motor

El motor se hace de cero para cumplir esto. Público: texturizado, animación y VFX. Se mide con `mbl_engine/quality.py`, salvo lo marcado como "falta medirlo".

| # | La malla o el motor debe | Se comprueba con |
|---|---|---|
| 1 | Ser solo quads, sin triángulos sueltos ni uniones en T | % de quads a la salida de S6 |
| 2 | No tener agujeros nuevos ni caras volteadas o dobladas; conservar ojos y boca | Agujeros nuevos, volteadas, dobladas |
| 3 | No tener geometría no-manifold | Topología de S6 |
| 4 | Pegarse al original, sin saltar de capa en dedos y axilas | Error de forma |
| 5 | Conservar las piezas finas (unas 3 aristas a lo ancho, por calibrar) | Error máximo; falta medirlo por pieza |
| 6 | Marcar los cantos duros | Falta medirlo: cantos conservados |
| 7 | Poner densidad según el detalle, sin saltos bruscos | Saltos de tamaño |
| 8 | Cerrar los anillos en tubos, sin espirales | % en loops cerrados; falta medirlo: anillos por tubo |
| 9 | Tener solo los polos necesarios | Polos frente a Maya |
| 10 | Dejar los polos fuera de cara, articulaciones y silueta | Falta medirlo: % de polos en zonas caras |
| 11 | Tener quads parejos | Quads deformados |
| 12 | Animación: unos 3 loops por articulación (por calibrar), loops en ojos y boca, simetría | Falta medirlo |
| 13 | Acercarse al número de caras pedido | Caras frente a objetivo |
| 14 | Dar siempre el mismo resultado con la misma malla | Huella del resultado |

Los números 8, 9 y 10, junto con el 11, son los que separan una malla correcta de una buena: tienen prioridad cuando haya que elegir. Antes de la fase 2 hay que añadir al juez lo que falta medir.

## Qué se rehace y qué se queda

Se rehace de cero todo el motor. No se usa nada de QuadriFlow, Instant Meshes ni de `best_retopo`. `mbl_core` ya está escrito desde cero y es la base.

Se quedan solo para medir: `tests/bench.py`, `baseline.json` y los jueces. El motor actual y Maya siguen en el plugin hasta que el nuevo los supere en el banco; no se borran antes.

## Decisiones ya tomadas

- Todo el núcleo es código nuestro en C++. Nada de programas externos en el plugin; lo abierto con licencia MIT/BSD se puede integrar con crédito. Nada GPL.
- Sin fuerza bruta: se analiza, se decide y se hace **una pasada**. Solo se rehace una zona que el juez marque, con un motivo medido.
- Proceso aparte de Maya: si falla, Maya no se cae.
- No invasivo: prioridad baja, tope de memoria del sistema, cancelable, hilos limitados.
- Determinista: la misma malla da el mismo resultado.

- **Polos, los mínimos que sostengan la malla:** son un objetivo que se mide, no un efecto secundario. S3 elimina los pares +/- que no hacen falta para seguir la forma. Se comprueba que quitarlos no suba el error de forma ni deforme los quads.
- **UVs a partir de la retopología:** una buena malla da casi las UVs. Se reutiliza lo que ya hay (costuras, enderezar, empaquetar). Pero el aplanado de hoy es Unfold3D, de Maya: seguirá siendo el respaldo mientras los parches no lo hagan innecesario, y se quita solo si el banco lo confirma.
- **Tipo de modelo en la ventana:** "Humano" u "Otro".
  - **Humano:** el usuario coloca guías (cara, ojos, boca, articulaciones) y el motor aplica los anillos que tocan. Va primero: no pesa y da control.
  - **Otro:** una IA reconoce qué es cada parte. Va después y como módulo opcional fuera del núcleo. Antes de empezarlo hay que decidir de dónde sale el modelo y con qué licencia, cuánto ocupa y si corre en un PC normal sin invadirlo.
  - Ninguno de los dos cambia el núcleo: ambos acaban en las mismas restricciones de S1 (guías, zonas, sin polos).

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
7. **Entradas completas y motor único:** modo Humano con guías, materiales, pincel, zonas sin polos.
8. **Modo Otro con IA**, como módulo opcional.

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
