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

## Referencia: la malla del artista

`human.obj` es la referencia de cómo debe quedar un humano. **El objetivo es llegar a ella, a sus 14.930 caras, sin bajar el listón a una malla más ligera.** Medida el 5 de noviembre, frente al banco a unas 5.000 caras:

| Malla | Caras | Polos | Polos por 100 caras | Aristas en loops cerrados |
|---|---|---|---|---|
| Artista (`human.obj`) | 14.930 (70 triángulos) | 1.160 | 7,8 | 20,6 % |
| Maya | 5.095 | 174 | 3,4 | 18,6 % |
| Motor MBL actual | 4.667 | 166 | 3,6 | 10,3 % |

Conclusiones, con la cautela de que las densidades son distintas y el humano tiene 16 piezas:

- **Contar polos no basta:** el artista tiene más por cara que Maya. Lo que importa es dónde están y a qué sirven. El requisito 10 pesa tanto como el 9.
- **El % de loops cerrados tampoco basta:** Maya se acerca al artista en esa cifra. Hacen falta medidas por zona: anillos por tubo y loops en ojos, boca y articulaciones.
- Falta medir el artista con las mismas medidas que el juez, y etiquetar dónde caen sus polos: dice cómo debe ser el resultado.

Cómo se usa en la prueba:

- **La entrada no puede ser la propia malla del artista:** sus aristas ya llevan el flujo y el resultado estaría regalado. Se prueba con una versión que lo borre (subdividida, triangulada y reducida o rehecha sin seguir sus aristas), y lo mismo con cualquier original de alta resolución si aparece.
- **Prueba de humano:** 15.000 caras pedidas. Se compara con el artista: polos (cuántos y a qué distancia de los suyos), anillos por tubo, loops en ojos, boca y articulaciones, y parches.
- **Es la referencia de los humanos, no de todo:** para no ajustar el motor solo a esta malla, el resto del banco se juzga con los requisitos generales.

## Medido el 9 de noviembre

**El artista, con el juez actual** (`tests/core_reference.py`): 7.869 parches si se traza desde todos sus polos, con mediana de 1 cara por parche (5.704 parches de una sola cara). El juez le pone **rojo**: 17,6 % de quads deformados (límite 10 %), aspecto medio 2,9 y 341 caras dobladas.

- Sus 1.160 polos no forman una estructura de parches grandes: muchos son pares 3-5 de ajuste local. **S4 no puede trazar desde todos los polos**, solo desde los del campo (S3), y dejar los pares de ajuste para S6-S7.
- El juez castiga cosas que el artista hace a propósito (quads alargados donde conviene). Antes de usarlo como criterio del motor nuevo hay que calibrarlo con esta referencia: no se pueden subir umbrales para aprobar, pero sí medir lo que de verdad importa (requisitos 8-11).

**El campo sobre una entrada sin flujo.** `Desktop/geo/human_noflow.obj`: la malla del artista rehecha con `polyRemesh` en 129.773 triángulos isótropos, sin rastro de sus aristas. `mbl_core` a 15.000 caras pedidas: 0,6 s, 75 MB.

| | Polos | A menos de 2 aristas de un polo del artista | Distancia mediana |
|---|---|---|---|
| Campo (S3) | 325 | 98 % | 0,4 aristas |
| Puntos al azar (para comparar) | 325 | 21 % | 5,1 aristas |
| Artista | 1.160 | (de los suyos, 62 % tienen uno del campo cerca) | — |

El campo pone sus polos donde el artista pone los suyos, y menos: es justo lo de "solo los que sostienen la malla". Por zonas (pies-rodillas / muslos-cadera / torso-manos / cuello-cabeza): artista 396 / 30 / 402 / 332, campo 97 / 4 / 81 / 143.

## Primera malla de quads (9 de noviembre)

`mbl_core` ya hace todo el camino: entrada → S2 → campo → parches → enteros → quads → relajado. Un solo proceso, un hilo, sin programas externos. Para mirar el resultado: `mayapy tests/quad_view.py quads.obj imagen.png` (rojo = polo de 3, azul = polo de 5 o más, amarillo = borde).

| Malla | Caras pedidas | Resultado | Polos | Tiempo | Memoria |
|---|---|---|---|---|---|
| Cubo, toro, esfera regular, cápsula | 300-800 | Cerrados, 100 % quads | 8 / 0 / 8 / 8 (como un cubo redondeado) | < 0,3 s | < 20 MB |
| Human sin flujo (130 k triángulos) | 15.000 | 15.149 quads, 100 % quads | 247 | 12 s | 204 MB |
| Mujer (`goodretopo.obj`, 450 k) | 5.000 | 5.281 quads | 227 | 14 s | 241 MB |
| Prop (`prop.obj`, hard surface) | 5.000 | 5.169 quads, cantos respetados | 53 | 7,5 s | 135 MB |

**Human frente al artista**, con el juez actual (`tests/core_reference.py`):

| | Motor nativo | Artista |
|---|---|---|
| Quads | 100 % | 99,5 % |
| Quads deformados | 20,3 % | 17,6 % |
| Caras dobladas | 44 | 341 |
| Aristas en loops cerrados | 35 % | 21 % |
| Polos | 181-247 | 1.160 |
| Nuestros polos a menos de 2 aristas de uno suyo | 95 % | — |

Cómo funciona cada etapa (decisiones que salieron de las pruebas):

- **S2** prepara la entrada: divide por el lado más largo y voltea hacia Delaunay hasta tener 2 aristas de entrada por arista final. Sin esto, en mallas de triángulos alargados (CAD, esfera UV) las líneas salían en ángulos falsos. También calcula el **tamaño local**: como mínimo 8 quads alrededor de un tubo, sin contar los cantos y hasta 4 veces más denso. Lo que se añade en cara y manos se quita del cuerpo, como hace el artista.
- **S3** lleva el tirón de curvatura en todos los niveles: sin él, en un cilindro o un toro cualquier giro del campo es igual de suave, el campo se tuerce unos 2° y los lazos no cierran.
- **S4** saca de cada polo sus 4−k separatrices con el modelo θ(α) = (k/4)·α + c, y las avanza todas a la vez (grafo de motocicletas). El paso corrige el error lateral acumulado para no derivar. Si una línea encuentra de frente otra cabeza o un polo, se une a ella. Las esquinas se reparten por nodo, de forma que todas sus cuñas sumen 4 − k. Una esquina dudosa pasa de un parche de 5 a uno de 3 por el camino más barato. Los parches diminutos se absorben, se podan las líneas colgantes y, como último recurso, un disco se trata como rectángulo con las 4 esquinas que mejor igualan los lados opuestos.
- **S5** reparte por mínimos cuadrados, redondea por tandas y repara insertando filas de quads con Dijkstra. Una tira puede valer 0 y entonces se colapsa: así desaparecen las tiras finas entre líneas casi paralelas. Si el total se aleja de lo pedido, se ajusta la arista y se repite.
- **S6** usa un mapa de Tutte de cada parche a su rectángulo (sin pliegues por construcción). Después se cosen las tiras colapsadas y se limpian almohadas y dobletes.
- **S7** relaja en el plano tangente y reproyecta a la superficie, con cantos y bordes fijos.

Pendiente, por orden:

1. **Agujeros que quedan**: en el humano, unos 8 parches (ojos, boca, dedos de los pies) aún no se rellenan, además de los bordes que ya trae la entrada.
2. **Densidad del cuerpo**: el torso sale más denso y con quads más alargados que los del artista.
3. **Loops en articulaciones, ojos y boca**: el artista mete anillos en rodillas y codos. Es el modo Humano con guías (fase 7).
4. **Velocidad**: S2 (2,9 s) y las rondas de partir (unos 4 s) se pueden recortar.
5. **Fase 5**: el adaptador en el plugin.

## Aberturas y guías

- **Aberturas:** por defecto se conserva todo lo que sea borde. Opción "cerrar agujeros pequeños", apagada por defecto.
- **Guías del usuario:** puntos que dicen qué es boca, ojos o articulaciones, con una experiencia parecida al Quick Rig de Maya (colocar puntos sobre el modelo, con simetría). Es la entrada del modo Humano.
- **Las mismas guías sirven para probar:** marcar los modelos del banco es lo mismo que usar la herramienta. El formato se define pronto, con el análisis; la herramienta en la ventana llega con la fase 5. Hasta entonces, localizadores de Maya con nombre (`boca`, `ojo_L`, `codo_R`...).

## Modelos de prueba

Van en `Desktop/geo`, sin subirlos al repositorio, con una línea cada uno en `tests/bench_models.json`. Objetivo: 10-20 modelos con humanos, escaneos sucios, mallas de IA, dedos que se tocan, ropa con capas y piezas muy finas.

## Cómo deben salir las UVs

Referencias de Marc (5 de noviembre): una cara en una sola isla, con la cuadrícula siguiendo los loops, y una pieza dura con islas rectangulares ordenadas. Lo que pide, y que el juez de UVs aún no mide (el águila con islas dentadas sale en verde):

- **Bordes de isla limpios:** siguen edge loops, sin dientes. Falta medirlo: fracción del borde en línea recta o sobre un mismo loop.
- **Pocas islas, y grandes:** una pieza con sentido (cara, ala) es una isla. Falta medirlo: islas diminutas y tamaño frente a lo que da la anatomía.
- **Cuadrícula alineada:** los loops van rectos en U y V dentro de la isla, y las piezas de rejilla salen rectangulares. Falta medirlo: desviación de las aristas respecto a los ejes.

Un poco de estiramiento es aceptable a cambio de esto. Con parches debería salir casi solo: las fronteras de parche ya siguen loops y cada parche ya es una rejilla.

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

## S4-S6: por parches (decidido)

Decisión del 5 de noviembre: trazar el campo, dividir la superficie en parches de cuatro lados, repartir enteros y rellenar con rejillas.

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

**Polos del campo, aún sin medir contra la referencia.** Estas cifras son del humano con 5.000 caras pedidas (Maya 174, Motor MBL 166), y a esa densidad sobran. A 15.000 caras el artista tiene 1.160, así que la cifra de partida no es alarmante por sí sola: falta ver si están en los sitios buenos.

| Malla de entrada al campo | Polos |
|---|---|
| Humano original, ángulo de canto 40° | 542 |
| Humano original, ángulo de canto 100° | 479 |
| Reducido a 20.000 triángulos, 80° | 333 |
| Reducido a 10.000 triángulos, 80° | 287 |
| Reducido a 5.000 triángulos, 80° | 230 |

Conclusiones: los cantos no son la causa principal, y reducir ayuda pero no basta. Si sobran o están mal puestos, falta probar: suavizar la geometría que ve el campo, eliminar pares de polos +/- cercanos, y medir aparte las piezas pequeñas (ojos, dientes: el humano tiene 16). Ojo: estas mallas de entrada conservan el flujo del artista, y los polos del campo no son aún los de la malla final: la cifra que decide es la de S6, con la entrada sin flujo.

## Fases

Cada fase acaba en algo que se puede medir. **Primero una vertical simple de punta a punta** y luego se mejora cada etapa: así hay quads cuanto antes y el riesgo de S4 se ve pronto.

1. **Referencia y campo.** Medir el artista: juez, polos por zonas y, trazando desde sus polos, en cuántos parches queda dividido. Crear la entrada sin flujo. Integrar `engine/mbl_decimate` en el núcleo. *Listo si:* humano a 15.000 caras con un nº de polos del orden del artista (sin pasar de 1.160) y cerca de los suyos, y el trazado da un nº de parches del orden del suyo.
2. **Vertical sobre mallas fáciles.** S4-S6 mínimos con enteros simples. *Listo si:* cubo, esfera y toro salen 100 % quads, sin agujeros, y el toro con todos sus anillos cerrados. **Hecho el 9 de noviembre** (`tests/test_core.py`, pruebas `test_quads_*`).
3. **Modelos reales.** Trazado robusto y enteros globales. *Listo si:* los 5 modelos del banco salen sin agujeros ni caras volteadas. Aquí se decide si la vía de parches se queda. **En curso**: humano, mujer y prop ya salen; quedan agujeros pequeños.
4. **Calidad.** S2 por zonas, S7 y S8. *Listo si:* fealdad ≤ Maya y anillos cerrados ≥ Maya en humano y criatura, a ≤ 2 veces el tiempo de Maya.
5. **Entrar en el plugin.** Adaptador en Python, opción experimental en la ventana, respaldo a Maya. *Listo si:* suites en verde. **Empezada el 9 de noviembre**: opción «Motor nativo (en pruebas)» en la ventana (`retopo._native_engine`), que exporta la copia preparada, lanza `mbl_core` (prioridad baja, con tope de memoria) y pasa el resultado por los arreglos, el relajado y la reproyección del plugin, sin su limpieza de polos. Prueba `test_native_engine_sphere`: una esfera de Maya sale como la esfera de cubo del Platonic Solid (8 polos de 3). Falta el respaldo automático a Maya si falla.
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
