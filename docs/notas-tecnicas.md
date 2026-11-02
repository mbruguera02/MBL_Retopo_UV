# Notas técnicas de MBL Retopo UV

Cuaderno de desarrollo: cómo funcionan por dentro los ajustes del plugin, qué se midió para elegir cada valor por
defecto y cómo está construido el Motor MBL. **No hace falta leerlo para usar el plugin**; la guía de uso está en el
[README](../README.md). Las cifras salen de las pruebas de octubre de 2026 con un humano (CC) y un águila (Meshy,
528.000 triángulos) y algunas son de versiones anteriores del motor. La investigación más reciente del Motor MBL
está en `docs/superpowers/specs/2026-10-30-motor-mbl-anillos-design.md`.

## Semáforo de estado

Al terminar cada proceso, el panel **Estado** muestra cada malla y cada paso con un color:

- **Verde (listo)**: puedes pasar a Substance sin tocar nada.
- **Amarillo (revisar)**: se puede usar, pero hay algo mejorable.
- **Rojo (arreglar)**: hay un problema que se notará al texturizar.

Debajo de cada punto amarillo o rojo aparece **→ qué tocar**: el ajuste exacto de la ventana que conviene cambiar, o el botón que hay que pulsar. El mismo resumen sale en el log. Doble clic en una malla la selecciona en la escena. **Comprobar estado** calcula el semáforo de las mallas seleccionadas en cualquier momento, sin cambiar nada.

Qué se comprueba:

| Paso | Rojo | Amarillo |
|---|---|---|
| Limpieza | geometría no-manifold sin reparar | caras de área cero, láminas, vértices sueltos o de 2 aristas |
| Retopo | no-manifold; menos del 85 % de vértices regulares; error de forma medio > 0,6 % o máximo > 5 % | agujeros que el original no tiene; < 98 % quads; < 92 % de vértices regulares; > 0,25 % de polos de 6+ aristas; error medio > 0,25 % o máximo > 2,5 % |
| UVs | sin UVs o caras sin UVs; solapes; UVs en espejo; fuera del cuadro 0-1 (o islas cruzando UDIMs); < 35 % de textura aprovechada; > 20 % de caras estiradas | < 50 % de textura aprovechada; > 10 % de caras estiradas más de 1,5x |

Los umbrales salen de las pruebas con el humano y el águila: con los ajustes por defecto, los dos salen en verde.

Hay dos excepciones:
- Con **Costuras escondidas**, la textura aprovechada se mide con umbrales más bajos (35 % y 25 %), porque ese modo cambia resolución por costuras ocultas.
- En piezas de menos de 200 vértices no se avisa por polos: un cubo tiene 8 polos inevitables.

## Parámetros

**Limpieza**
- *Fusionar vértices*: une los vértices que estén más cerca que esa distancia (en unidades de la escena, normalmente cm). Las costuras UV que ya tuviera la malla se conservan.

**Retopología**
- *Motor*:
  - **Automático** (por defecto): hace el retopo con Maya y con QuadWild y se queda con el resultado que tenga menos polos por cada 100 caras, sin agujeros. Para elegir QuadWild exige que tenga al menos un 5 % menos de polos por cara y como mucho el doble de error de forma. El log muestra las cifras de los dos y cuál se ha elegido. Tarda lo que los dos juntos (de 30 s a 1,5 min).
    - Humano a 3.000 caras: elige Maya.
    - Humano a 8.000 caras: elige QuadWild, con 217 polos frente a 319.
    - Águila: elige Maya, porque QuadWild deja agujeros.
    - Si tenías los ajustes guardados de una versión anterior, el motor pasa a Automático una vez; después se respeta lo que elijas.
  - **Maya (polyRetopo)**: el más rápido de los buenos.
    - Va con *regularidad de topología* 1 (Maya trae 0,5). Se eligió con un barrido de sus parámetros:
      - **Humano a 2.000 caras:** polos de 157 a 142 y error medio del 0,207 % al 0,195 %. Las aristas en loops cerrados suben del 15 % al 25 %.
      - **Águila a 5.000 caras:** polos de 593 a 560 y polos de 6+ de 20 a 13.
      - Anisotropía, uniformidad de caras y desviación de aristas no mejoraron nada.
    - **Agujeros nuevos:** polyRetopo abre agujeros en zonas finas, como 5 en las plumas del águila. Unfold3D los ve como UVs no-manifold. El plugin compara los bordes del resultado con los del original y cierra con quads solo los que el original no tenía. Las aberturas reales del modelo, como cuello o mangas, se dejan.
  - **QuadWild (por parches, experimental)** divide el modelo en parches y reparte los quads en cada uno minimizando los polos. **Instant Meshes** es el más rápido. Más detalles sobre QuadWild abajo.
- *Simetría*: viene en **No** (por defecto, el modelo entero). Con **Automática**, si el modelo es simétrico en X se hace el retopo de la mitad +X y se refleja, soldando la costura central. Los dos lados quedan con exactamente la misma topología: polos y loops en el mismo sitio, como se espera de un personaje. También permite pintar en simetría.
  - **Cómo detecta la simetría:** compara el modelo con su reflejo en el plano X central. Lo considera simétrico si la distancia media es menor del 0,3 % del tamaño. El humano da un 0,005 % y el águila un 0,06 %; los ejes Y y Z dan entre el 2 % y el 14 %.
  - **Humano a 5.000 caras:** reflejo exacto, mismo error de forma (0,070 % frente a 0,081 %), polos parecidos (242 frente a 216) y más rápido (20 s frente a 31 s).
  - **Águila:** también simétrica, con los mismos polos. Como el original no es perfectamente simétrico, el error de forma sube un poco (0,133 % frente a 0,124 %).
  - **Siempre en X** fuerza la simetría aunque el modelo no lo sea del todo. **No** la desactiva.
  - **Red de seguridad (Motor MBL):** con la simetría activada el motor prueba además variantes sin ella (una por cada tres; con calidad alta, la mitad de las variantes: 6 simétricas y 3 sin simetría) y entrega la simétrica solo si no deja más fallos visibles (caras volteadas o dobladas, deformadas...) que la mejor sin simetría; si deja más, entrega la asimétrica y lo avisa en la ventana.
  - El plano es el centro del modelo en X. Si tu modelo es simétrico en otro eje, gíralo antes.
- *Guías de flujo*: trazos sobre el modelo que indican por dónde deben ir los loops, por ejemplo alrededor de los ojos, de la boca o de una costura de ropa.
  - **Cómo se dibujan:**
    - Selecciona el original y pulsa **Dibujar guías**. El modelo se vuelve «vivo» y los trazos del lápiz se pegan a su superficie.
    - Al acabar, pulsa **Terminar guías**. Los trazos quedan amarillos, en el grupo `<malla>_mblGuides`.
    - **Usar curvas seleccionadas** añade curvas que ya tengas.
    - **Borrar guías** las quita.
  - **Cómo funcionan:** el plugin marca sobre una copia del modelo el camino de aristas que sigue cada trazo y lo pega al trazo. El retopo de Maya tiene ese camino como rasgo que respetar. En la esfera de prueba, sin guía ninguna arista sigue el aro; con guía sale un loop entero sobre él, con el mismo número de caras.
  - **Motor:** con guías, el motor Automático usa Maya, que es el que mejor las sigue. En la esfera de prueba, Maya pone 36 aristas sobre el aro con 8 polos. Nuestro Instant Meshes también sigue las guías: las recibe como direcciones del campo (`--orient`) y pone 22 aristas con 41 polos, frente a 2 sin guía.
  - **Simetría:** con guías, el retopo se hace sobre el modelo entero y no por mitades. Hacerlo por mitades con guías a veces disparaba las caras (en la esfera de prueba, 2.700 con 800 pedidas).
  - **Red de seguridad:** si aun así salen más de 1,6 veces las caras pedidas, se repite sin guías y el log lo avisa.
- *Pincel de detalle*: zonas que necesitan más caras, como la cara o las manos.
  - **Cómo se pinta:** selecciona el original, pulsa **Pintar detalle** y pinta en rojo con la herramienta de pintar colores de Maya. Se guarda en el color set `MBL_detalle`, aparte de los colores del modelo. **Borrar detalle** lo quita.
  - **Densidad en lo pintado:** por defecto ×4, es decir, cuatro veces más caras por unidad de área. Lo no pintado mantiene su densidad, y las caras extra se suman al total.
  - **Motor:** el retopo de Maya no admite densidad variable. Lo probé de cuatro formas: agrandar la zona, darle más resolución de entrada, darle rugosidad falsa y bajar la uniformidad. Ninguna cambia el reparto. Por eso, con zonas pintadas se usa nuestra versión de Instant Meshes con densidad pintada (ver `engine/README.md`).
  - **Humano a 5.000 caras con la cabeza pintada (×4):** la cabeza recibe 1.732 caras, frente a 828 con Maya y 356 con Instant Meshes sin pintar. El total sube a 7.392. Instant Meshes sigue la forma peor que Maya (error medio 0,18 % frente a 0,07 %), así que pinta solo donde haga falta.
  - **Si hay guías y detalle a la vez:** Instant Meshes usa las dos cosas. Antes las guías se ignoraban.
- *Caras objetivo*: número aproximado de caras del resultado. Para un personaje medio, entre 5.000 y 20.000.
- *Ángulo aristas duras*: las aristas con un ángulo mayor se respetan como bordes marcados. Con 0 se desactiva, que es lo habitual en mallas orgánicas.
- *Suavizado*: pasadas de suavizado de Instant Meshes. Por defecto, 2.
- *Solo quads*: malla 100 % de quads. Si lo desmarcas, admite algún triángulo y sigue mejor la forma.
- *Determinista*: con la misma malla y los mismos ajustes, el resultado es siempre idéntico. Algo más lento.
- *Alinear a los bordes abiertos*: solo tiene efecto si la malla tiene agujeros o bordes.
- *Reproyectar sobre el original*: pega cada vértice nuevo a la superficie original para no perder volumen.
- *Limpiar polos*: quita parejas de polos (vértices de 3 y 5 aristas) girando aristas y cerrando quads, igual que se haría a mano. Solo acepta un cambio si reduce los polos y no deja quads doblados. Con el águila de prueba: de 447 a 372 polos, y los de 6 o más aristas de 14 a 4. Tarda unos 3 segundos.
- *Relajar quads*: pasadas que igualan el tamaño de los quads deslizándolos por la superficie del original. Además de dejar los loops más limpios, baja el error de forma (águila: del 0,145 % al 0,124 %). Los bordes y las aristas marcadas no se mueven, así que las esquinas de las piezas duras se conservan. 0 = no relajar.
- *Densidad por curvatura* (experimental, solo con Instant Meshes MBL): caras más grandes en zonas planas y más pequeñas en curvas, con el mismo número total de caras. 0 = uniforme (por defecto). Con 0.4, el error de forma bajó un 10–25 % en la mayoría de pruebas, pero añade polos. *Relación máx. de tamaño* limita cuánto pueden diferir las caras.
- *Ignorar piezas menores de*: los restos flotantes pequeños (muy habituales en mallas de IA o escaneos) no se envían a Instant Meshes, porque lo confunden. El original no se toca.
- *Rellenar agujeros de hasta*: los agujeros que dejen Instant Meshes o Maya con ese número de aristas o menos se cierran. Con Maya, solo los que no estaban en el original y con quads.
- *Tiempo máximo*: si Instant Meshes tarda más, se detiene y avisa. Si se queda bloqueado sin trabajar (le pasa a veces), se reinicia solo una vez.

**Diagnóstico**: el botón *Diagnóstico* mide el retopo seleccionado y lo escribe en el log:
- caras antes y después;
- porcentaje de quads y de vértices regulares;
- número de polos;
- **edge loops:** longitud media de los loops y porcentaje de aristas en loops cerrados (anillos). Un loop sigue recto por vértices de 4 aristas y se corta en un polo. Cuanto más largos y más cerrados, mejor sigue la malla la forma;
- error de forma máximo y medio frente al original, como porcentaje del tamaño del modelo.

Además colorea los polos: azul con 3 aristas, rojo con 5 y magenta con 6 o más. *Quitar colores* los borra; hazlo antes de exportar a Substance. Cada retopo escribe estas métricas en el log al terminar.

### QuadWild (motor por parches)

Usa [QuadWild-BiMDF](https://github.com/cgg-bern/quadwild-bimdf) (Universidad de Berna, licencia GPL-3). Sus binarios oficiales para Windows están en `mbl_retopo_uv/bin/quadwild`, y el código fuente en `engine/quadwild-bimdf`. El plugin lo llama como programa aparte, igual que a Instant Meshes.

Cómo funciona:
1. Calcula un campo de direcciones sobre una copia suavizada del modelo y traza los parches.
2. Reparte los quads en cada parche.
3. Ajusta el tamaño de quad hasta acercarse a las caras pedidas (hasta tres intentos).
4. Después pasa por la misma limpieza de polos, relajación y reproyección que los otros motores.

Resultados medidos (polos por cada 100 caras, menos es mejor):

| Malla | Caras | Maya | QuadWild |
|---|---|---|---|
| Humano | ~3.000 | 159 polos (5,5) | 234 polos (6,8) |
| Humano | ~8.000 | 319 polos (3,7), error 0,038 % | **217 polos (2,9)**, 97 % de vértices regulares, error 0,061 % |
| Águila (IA, 528k) | 5.000–9.000 | 372–518 polos | deja agujeros: se usa Maya |

En QuadWild, el número de polos depende casi solo de la forma del modelo, no del número de caras. Por eso compensa con muchas caras y en mallas limpias.

Si QuadWild deja agujeros que el original no tiene (pasa en mallas sucias como el águila), el plugin lo avisa en el log y hace el retopo con Maya automáticamente.

Tarda entre 20 y 90 s.

Después de Instant Meshes, la herramienta repara automáticamente su resultado: quita caras rotas o duplicadas, elimina los vértices de solo 2 aristas, separa los vértices "pajarita", orienta todas las caras hacia fuera (las caras negras del viewport) y borra los restos sueltos. El log indica qué ha arreglado.

**UVs**
- *Respetar costuras existentes*: si la malla ya tiene costuras UV (las has hecho tú), se usan esas y no se añade ninguna. Desmárcalo si las UVs que trae el FBX no sirven.
- *Ángulo de costura*: corta por las aristas más dobladas que este ángulo. Por defecto es 180 (no corta por ángulo), que es lo mejor para orgánicos: con un águila de prueba, 80° daba 290 islas y 180° unas 15. Bájalo (70°–90°) para piezas duras con esquinas marcadas.
- *Costuras automáticas de Unfold3D*: añade solo los cortes necesarios para desplegar sin solapes (por ejemplo, para abrir una esfera o un tubo).
- *Cortar tubos*: abre brazos, dedos o cuellos para que no queden retorcidos.
- *Dividir islas*: 0 da el mínimo de islas; valores altos dan más islas y menos estiramiento. Por defecto es 0,75: el cuerpo sale por partes (brazos, piernas, torso, cabeza). Con el humano de prueba, la textura aprovechada sube del 29 % al 61 % frente a 0. Por encima de 0,75 no cambia nada.
- *Costuras*:
  - **Máxima resolución** (por defecto): costuras de Unfold3D. Da más islas y aprovecha al máximo la textura. Además, las islas que ocupan más del 12 % del área se vuelven a cortar, porque las islas enormes empaquetan mal. Con el águila, la textura aprovechada pasa del 42 % al 70 % y las caras estiradas del 8 % al 4 %.
  - **Costuras escondidas**: lleva las costuras a zonas poco visibles (por detrás, axilas, entrepierna), las endereza para que sigan los loops, une las islas diminutas y no corta por los ojos ni por la boca. Con el humano: de 39 a unas 15 islas, costuras de brazos y piernas por detrás y un 34 % menos visibles, a cambio de un 10 % menos de textura aprovechada. El modelo debe mirar hacia +Z, como es habitual en Maya. Si esconder las costuras estira demasiado la textura (pasó con las plumas del águila), se vuelve solo a las de Unfold3D y el log lo avisa.
- *UVs simétricas* (activado): si el modelo es simétrico en X, por ejemplo tras el retopo simétrico, las costuras de un lado se copian en el otro. Cada isla tiene su gemela con la misma forma (brazo izquierdo y derecho), y el torso y la cabeza quedan simétricos.
- *Costuras de sastre* (activado), sobre las costuras de Unfold3D:
  - **Cortes de tubo:** el corte que abre cada tubo (torso, brazos, piernas) se lleva por el camino menos visible entre sus dos bordes: espalda, axilas, interior y parte de atrás de las piernas.
  - **Centro del pecho y la cara:** la costura central por delante se quita, y se añaden por detrás los cortes imprescindibles.
  - **Tramos rectos:** se enderezan para que sigan los loops en vez de hacer escalera.
  - **Islas diminutas:** se cosen a su vecina.
  - **Tolerancia por isla:** después de desplegar, se compara isla por isla con las costuras de Unfold3D. A las islas que ganan caras estiradas se les devuelven sus costuras originales y se vuelve a desplegar; el resto se queda con las de sastre. Con una malla del humano: 5 islas devueltas y el resto aceptado, costuras un 44 % menos visibles y 5,3 % de caras estiradas. Antes se descartaban todas, con un 12–24 % de caras estiradas.
  - **Red de seguridad:** si aun así estira demasiado, se dejan todas las de Unfold3D.
  - Las islas diminutas que no son discos, restos del retopo, ya no bloquean el sastre. En el humano completo, dos islas de 2 caras hacían que no se moviera ninguna costura.
  - **Humano:** de 45 islas, con costura por delante del pecho y escalones, a 20 islas con las costuras por detrás. El estiramiento pasa del 6,5 % al 8,5 % de caras (sigue en verde en el semáforo).
- *Islas rectas y de pie* (activado): antes de empaquetar, cada isla se gira para que el arriba del modelo quede arriba en la textura. El empaquetador solo puede dejarla así o tumbarla 90°, nunca torcida ni boca abajo. Con el humano, la textura aprovechada se mantiene en torno al 60 %.
- **Chequeo de sanidad antes de las UVs:** al terminar el retopo se buscan caras con un ángulo interior de menos de 20° o con menos del 5 % del área mediana. Sus vértices se relajan sobre el original, aunque estén en un pliegue, porque la relajación normal deja quietos los pliegues para no redondear piezas duras. En el águila, esas caras eran las 8 con UVs solapadas: 6 tenían un ángulo de menos de 20° y 2 un área casi nula. Con el chequeo, los solapes pasan de 8 a 0.
- Pase lo que pase, si Unfold3D deja una isla que no se puede aplanar (por ejemplo, un toro abierto con un solo tajo), se añade el corte que falta por la zona menos visible. Con el toro de prueba, las caras muy estiradas pasaron del 88 % al 0 %.
- *Optimize*: iteraciones para reducir la distorsión. Por defecto 5: con 10 tarda un 15 % más y apenas mejora. Al terminar, el log indica el *estiramiento*, es decir, el porcentaje de caras cuyo tamaño en UV difiere más de 1,5 veces del que tienen en 3D.
- *Resolución textura* y *Padding*: el padding se indica en píxeles a esa resolución. 16 px a 4K es un valor seguro para Substance.
- *Calidad de layout*: con más calidad se aprovecha mejor el espacio, pero tarda más.
- *Intentos de empaquetado*: el empaquetado de Unfold3D cambia mucho con sus ajustes (con el mismo humano, entre el 48 % y el 66 % de la textura). Se prueban varias combinaciones sobre una copia y se queda la que más aprovecha. Con 3 se gana un 5–15 % de resolución a cambio de unos 5 segundos; con 1 es lo más rápido.
- *Máx. caras para UVs*: si la malla tiene más caras, no se hacen UVs y se avisa. Unfold3D sobre medio millón de caras puede agotar la memoria y cerrar Maya. Haz antes el retopo. Con 0 no hay límite.
- *UDIM*: reparte las islas en varios tiles (1001, 1002…). Sin UDIM, todo va al tile 0–1 y la densidad se calcula sola y es uniforme en toda la malla.
  - *Nº de UDIMs*: solo dices cuántos tiles quieres, y el plugin calcula la densidad de texel que los llena. Por defecto son 4. Con 4 UDIMs, el águila pasa de 150 a 228 px/cm, y el humano de 20 a 31 px/cm, frente a un solo tile.
  - Si la isla más grande ya ocupa un tile entero, más tiles apenas ganan resolución, y el log lo avisa. Eso pasa con el torso del humano: 8 UDIMs dan 33 px/cm, casi lo mismo que 4.
  - Con *Nº de UDIMs* a 0 se usa la *densidad de texel* que indiques (píxeles por cm), útil para que varias piezas compartan la misma densidad.
  - En Substance Painter, crea el proyecto con *Use UV Tile workflow* activado.

Después de cada paso de UVs, el log muestra cuántas islas hay, la densidad de texel y si queda algún solape, alguna isla en espejo o alguna UV fuera de 0–1.

## Motor MBL (núcleo sin Maya)

`mbl_engine/` es el núcleo del Motor MBL. Solo usa Python y numpy, sin nada de Maya, para poder usarlo más adelante en Blender, Cinema 4D u otros programas. El plugin de Maya es su primer adaptador. Hasta ahora contiene:

- **Malla** (`mesh.py`): puntos y caras de cualquier número de lados, con aristas, bordes y sus lazos, valencias, normales, áreas y triangulación.
- **Punto más cercano** (`closest.py`): sustituye a la intersección de Maya. Reparte los triángulos en una rejilla y busca por anillos de celdas, todas las consultas a la vez y por bloques para no disparar la memoria. Es exacta: en las pruebas coincide con mirar todos los triángulos uno a uno.
- **Métricas** (`metrics.py`): topología, polos, edge loops y error de forma, con los mismos criterios que el diagnóstico del plugin. El diagnóstico ya usa los loops del núcleo.
- **Reparación** (`repair.py`): caras degeneradas, aristas compartidas de más, vértices de 2 aristas, orientación, pajaritas, restos sueltos, cierre de agujeros (también solo de los nuevos) y lectura y escritura de OBJ. Es el mismo código que usaba el plugin, movido al núcleo; el plugin lo importa de ahí.
- **Limpieza** (`cleanup.py`): detectar problemas (no-manifold, láminas, área cero, vértices sueltos o de 2 aristas), fusionar vértices cercanos y separar lo no-manifold. Sustituye a `polyInfo`, `polyMergeVertex`, `polySplitEdge` y `polySplitVertex`.
- **Simetría** (`symmetry.py`): detectar la simetría en X, cortar la mitad +X (sustituye a `polyCut`), reflejarla y soldar la costura. El plugin ya usa del núcleo la detección, el reflejo y la soldadura.

**Limpieza y simetría comparadas con Maya**, sobre los originales del humano (14.046 caras) y del águila (528.550 caras): los mismos problemas detectados (16 vértices no-manifold en el águila), los mismos vértices fusionados (169), el mismo error de espejo (0,0050 % y 0,0570 %) y el mismo corte por la mitad (7.205 y 264.351 caras, con la misma área y los mismos bordes). El núcleo tarda de 2 a 4 veces más que Maya: hasta 2,3 s en el águila.

- **Retopo** (`retopo.py`): el proceso completo sin Maya.
  - **Motor:** Instant Meshes o QuadriFlow.
  - **Después del motor:** se reparan la topología y solo los agujeros nuevos, se limpian los polos (`quadopt.py`), se relaja, se arreglan las caras degeneradas y se reproyecta sobre el original con la rejilla propia.
  - **Simetría:** con Instant Meshes se hace la mitad y se refleja, soldando la costura. Con QuadriFlow se hace el modelo entero, porque cierra los bordes y no queda corte que soldar.
  - **Ejecución de los motores** (`process.py`): límite de tiempo, detección de bloqueos y cancelación.
  - También se movieron al núcleo la limpieza de polos (`quadopt.py`) y las direcciones de curvatura (`orient.py`).

**Retopo del núcleo frente al de Maya** (polyRetopo con todo el proceso del plugin), a 5.000 caras:

| | Polos | Polos 6+ | Aristas en loops cerrados | Error medio | Error máx. | Tiempo |
|---|---|---|---|---|---|---|
| Humano: Maya | 190 | 2 | 24 % | 0,072 % | 0,77 % | 3 s |
| Humano: núcleo + Instant Meshes | 236 | 18 | 11 % | 0,272 % | 2,97 % | 2 s |
| Humano: núcleo + QuadriFlow | 140 | 0 | 0,6 % | 0,207 % | 3,71 % | 7 s |
| Águila: Maya | 327 | 7 | 14 % | 0,133 % | 2,02 % | 13 s |
| Águila: núcleo + Instant Meshes | 546 | 40 | 1 % | 0,312 % | 6,28 % | 11 s |
| Águila: núcleo + QuadriFlow | 344 | 6 | 1,3 % | 0,236 % | 6,34 % | 27 s |

**Dónde estaba el error (29/10).** Medido en los dos sentidos, todo el error de forma venía de la etapa del motor y en un solo sentido: lo que el retopo no cubre del original (puntas de dedos, bordes de plumas, el remate de una cabeza). Del retopo al original el error ya era igual que el de Maya (0,024 %); del original al retopo era 3,4 veces peor (0,32 % frente a 0,095 %). Reducir la malla para el motor casi no cuesta (0,002 %) y proyectar los vértices solo arregla el otro sentido. Con eso se cambió la forma de trabajar: en vez de lanzar el motor varias veces y quedarse con la que sale bien por suerte, se le da una entrada que pueda representar.

- **Engordar las piezas finas** (`sizefield.py`). Un motor pierde o aplasta lo que es más fino que su arista. Se mide el grosor local de cada vértice (una bola que avanza por la normal hasta tocar otra parte de la superficie; en una malla reducida de 30.000 triángulos, una sola vez) y las piezas con menos de 1 a 2 aristas de grosor se engordan por la normal, sin pasar de un cuarto del hueco libre que tienen delante. El motor ve esa versión, y los vértices que saca se pegan después al original, así que la forma no cambia. En el humano a 5.000 caras el error medio baja de 0,19 % a 0,06 % y deja de depender de la semilla (antes 0,12 a 0,23 %). Un campo de tamaño con gradación limitada (`feature_density`) no ayudó a QuadriFlow: es caótico con él, así que queda para el extractor propio.
- **Ajuste final** (`fit.py`). Mira los puntos del original más lejos del retopo, tira de los vértices de la cara más cercana hasta ellos, los vuelve a pegar al original y descarta lo que da la vuelta a una cara, deja un quad casi plano o no mejora el error máximo en los dos sentidos. Los vértices de borde no se mueven. En el humano el error máximo baja de 1,43 % a 0,75 %, en 2 rondas y 2,6 s.
- **Elección de la variante.** Las variantes ya no son semillas al azar sino engordados distintos (grosor 2,0, 1,0 y 1,5 aristas). Gana la de menor coste: calidad de forma (error medio + 0,1 del máximo), polos y caras que no son quads; a igualdad, la de más loops cerrados. Solo valen las de caras dentro de 0,8 a 1,25 del objetivo y como mucho 1,6 veces los polos de la que menos tiene.
- **Número de caras.** QuadriFlow no respeta el total; se calibra con hasta 3 pasadas (la pendiente medida entre pasadas) y se queda la más cercana al objetivo. En nuestra copia de QuadriFlow se quitó el suavizado de la densidad con los vecinos cuando se da una propia.

**Núcleo con QuadriFlow, engordado y ajuste (mejor de 3), a 5.000 caras** (errores medidos con 40.000 puntos):

| | Caras | % quads | Polos | Polos 6+ | Aristas en loops cerrados | Error medio | Error máx. | Error p99 | Tiempo |
|---|---|---|---|---|---|---|---|---|---|
| Humano: Maya | 4.838 | 99,8 | 190 | 2 | 24 % | 0,077 % | 0,78 % | 0,49 % | 3 s |
| Humano: núcleo | 5.489 | **100** | **140** | **0** | 6,6 % | **0,055 %** | **0,50 %** | **0,30 %** | 13 s |
| Águila: Maya | 5.588 | 99,8 | 327 | 7 | 14 % | 0,155 % | 2,02 % | 1,34 % | 12 s |
| Águila: núcleo | 5.266 | **100** | 574 | **0** | 4,0 % | **0,071 %** | **1,13 %** | **0,36 %** | 22 s |

En forma ya le gana a Maya en los dos modelos (la mitad de error en el águila). Lo que sigue peor:

- **Tiempo:** 13 s y 22 s frente a 3 s y 12 s de Maya (4 y 2 veces más). Antes eran 40 s y 84 s: el 68 % se iba en buscar puntos más cercanos en Python (grosor, ajuste, proyección, medida). `mbl_geom` (C++) lo hace unas 800 veces más rápido (20.000 consultas: 7,9 s a 0,01 s en el humano; en el águila, de 113 s a 0,03 s). Quedan la reducción de la malla, QuadriFlow (1 a 3 pasadas para acertar el nº de caras) y la limpieza de polos.
- **Polos en el águila:** 407 a 647 según la variante, frente a 327: las plumas engordadas reciben muchas caras. La elección exige solo quads antes que pocos polos: la variante de 407 polos y error máximo 0,67 % deja 6 triángulos (99,89 % quads) y no se elige, porque la que sí es pura tiene 574 polos y error máximo 1,13 %.
- **Loops cerrados:** 2,5 a 6,6 % frente a 14 a 24 % de Maya: QuadriFlow hace espirales en lugar de anillos. No lo arregla el engordado ni un campo de orientación propio como pista para QuadriFlow (probado: ver el apartado de pruebas descartadas de `docs/superpowers/specs/2026-10-30-motor-mbl-anillos-design.md`).
- **Triángulos:** el humano sale con solo quads. En el águila, según la variante, quedan de 0 a 0,4 % (QuadriFlow deja agujeros en las plumas y un agujero de borde impar no se puede cerrar solo con quads); la elección prefiere una variante pura si existe.


**En el plugin (30/10).** El desplegable *Motor* tiene **Motor MBL (solo quads)** y el modo **Automático** lo incluye: hace el mejor de Maya y QuadWild (con su simetría) y el Motor MBL, y se queda el de menor coste (forma, polos y caras que no son quads). El Motor MBL hace siempre el modelo entero, no admite guías ni detalle pintado (en ese caso se usa el motor clásico) y usa `mbl_variants` (3) y `mbl_time_budget` (270 s) de los ajustes. QuadriFlow viene en `mbl_retopo_uv/bin/quadriflow.exe`. Sin él, el automático sigue como antes.

**Calidad de topología (30/10).** Las cifras de arriba solo medían forma (error, polos, quads) y no lo que se ve. `quality.py` mide también quads deformados, saltos de tamaño entre caras vecinas, caras dadas la vuelta o dobladas, agujeros nuevos, loops cerrados y simetría, con una nota (`grade`) y un número de "fealdad" (`defect_score`) que entra en la elección de variantes y en la comparación del plugin. Con ellas se vio que el Motor MBL era claramente peor que Maya en lo que ve el usuario (humano: 9,4 % de quads deformados frente a 0,8 %), y la causa principal era el ajuste final, que estiraba quads (4,9 % a 9,4 %). Ahora el ajuste y la proyección solo mueven un vértice si no empeoran los quads que toca (humano: 4,4 %; águila de 32 % a 4,8 %).

**Simetría del Motor MBL.** Se le da a QuadriFlow la mitad abierta con su opción `-boundary`, que alinea los quads con el borde del corte (antes se tapaba el corte con triángulos y se quitaban después: la costura central salía con unas 3 veces más caras dobladas y volteadas), y se refleja: el resultado es idéntico a su reflejo (error 0,0000 %) y queda un bucle central sobre el plano. Al unir los dos lados, un vértice de 2 aristas se funde en un solo quad (antes dejaba dos triángulos).

**Estado honesto.** Con las cifras de calidad, Maya sigue ganando en lo que se ve: humano, fealdad 1,0 frente a 4,2; águila, 5,8 frente a 9,3. QuadriFlow deja el campo menos regular que Maya (4 % de quads deformados incluso sin ningún ajuste). Además la tapa lo hace lento (de 13 s a 45 s por variante en el humano: el abanico de triángulos finos obliga a QuadriFlow a subdividir). Por eso el modo Automático ya solo elige el Motor MBL si su nota de calidad es mejor, y en la ventana está marcado como experimental. Pendiente: tapa con triángulos más gordos, campo de orientación propio para quads más regulares y más modelos de prueba.

Polyretopo sigue siendo el motor por defecto.

**Comparado con la versión de Maya**, sobre los retopos del humano (5.000 caras) y del águila (5.588 caras, original de 528.000 triángulos):

- **Caras, quads, % de regulares, polos de 3/5/6+ aristas y loops:** idénticos, uno a uno.
- **Error de forma medio:** 0,0817 % frente a 0,0808 % en el humano y 0,1465 % frente a 0,1471 % en el águila. La diferencia viene de que la intersección de Maya es algo aproximada.
- **Tiempo del error de forma:** 1,0 s en el humano y 2,3 s en el águila, frente a 0,2 s y 0,9 s con Maya. Más de la mitad es preparar la rejilla de los 528.000 triángulos.

Las pruebas del núcleo no cargan Maya. Basta cualquier Python 3 con numpy:

```
"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe" tests\test_engine.py
```

## Continuidad de la textura entre islas (2 de noviembre)

Queja: según los rayones o el ruido que se pongan en Substance, la textura se ve cortada y no continúa (sin «racord») al otro lado de un corte. Se midió sobre humano (5.000 caras) y águila (5.000), por cada arista de corte: el giro de una orilla respecto a la otra (ángulo entre sus dos vectores UV) y su escala (cociente de longitudes), ponderado por la longitud 3D del corte. «Rígido» = el reparto de giros a lo largo de la pareja de islas varía menos de 15°: las dos orillas tienen la misma forma y una rotación rígida basta para que casen.

| | Islas | Cortes | Textura | Rígido | Giro < 15° | Giro ~90° | Escala dentro de 10 % |
|---|---|---|---|---|---|---|---|
| Humano, empaquetador gira 90° (antes) | 54 | 7,5 % | 62,6 % | 24 % | 14 % | 17 % | 60 % |
| Humano, sin girar (ahora) | 36 | 7,5 % | 65,8 % | 24 % | 20 % | 9 % | 61 % |
| Águila, gira 90° (antes) | 64 | 12,8 % | 64,0 % | 15 % | 14 % | 21 % | 45 % |
| Águila, sin girar (ahora) | 56 | 12,1 % | 60,7 % | 11 % | 20 % | 16 % | 46 % |

- **Cambio hecho:** `uv_allow_rotation` pasa a `False` (ajustes versión 6). Las islas quedan de pie y el empaquetador no las tumba 90°. Mejora el giro entre islas vecinas y casi no cuesta textura (humano +3,2 puntos, águila −3,3).
- **Lo que no se puede arreglar girando:** solo el 24 % (humano) y el 11-15 % (águila) de la longitud de los cortes separa islas cuyas orillas casan; hasta la mitad separa orillas muy distintas (una recta y otra curva, como el hombro de un brazo recto). Elegir el giro de cada isla para que case con sus vecinas (probado en prototipo) sube «giro < 15°» del 20 % al 24 % y no merece la pena.
- **Menos islas no ayuda:** `uv_split_shells = 0` deja 8 islas en el humano, pero la textura aprovechada cae al 41 % (águila: 11 %) y el cociente de escala empeora (p90 de 1,4 a 1,9).
- **Escala:** a lo largo de los cortes, la escala difiere más de un 10 % en el 40 % (humano) y el 55 % (águila) de su longitud. Más iteraciones de Optimize (20 en vez de 5) no lo cambian en el humano; en el águila bajan algo los casos extremos (p90 de 2,7 a 1,9) sin tocar el resto.
- **Pendiente para la V2:** cortes que se parezcan por los dos lados (rectos, a lo largo de los tubos) y ajustar a la vez la longitud de las aristas de las dos orillas de un corte. Es un problema de elegir los cortes, no de colocar las islas.
- **Consejo para el usuario** (en el README): en Substance, proyección triplanar o de espacio mundo en las capas procedurales; lo pintado a mano y las máscaras de curvatura u oclusión no dependen de los cortes.

## Analizador de caras mínimas (2 de noviembre)

`mbl_engine/analyze.py` (numpy puro) dice cuántas caras hacen falta para no perder la forma del original. El plugin lo
llama antes de cada retopo (aviso en el log si se piden menos), en el semáforo (amarillo si el retopo tiene menos del
85 % del mínimo) y con el botón *Recomendar caras*. Tarda 0,3 s con el humano y unos 4-5 s con mallas de 528.000
triángulos (se mide en una versión reducida de 30.000).

- **Mínimo recomendado = curvatura.** El error de cuerda de una arista de longitud L sobre un radio R es L²/(8R), así
  que el error de forma medio de N caras iguales es proporcional a 1/N. La curvatura de cada vértice se estima con lo que
  giran las normales por unidad de longitud en cada arista (la que más gira, promediada con sus vecinos). El error real del
  Motor MBL es 2 veces el calculado (`CURVATURE_GAIN`), y se apunta a un error medio de 0,12 % de la diagonal más un margen
  de 1,3 para que el 1 % de peor error (p99) quede en torno al 1 % (`MINIMUM_MARGIN`).
- **Máximo detalle = grosor.** `sizefield.thickness` con pasos geométricos de 0,15 a 30 aristas de referencia; N con el
  que solo el 10 % del área de volúmenes queda más fino que 3 aristas. Es informativo: el águila y la criatura piden más de
  200.000 caras para esto y con 12.000 el Motor MBL ya da 0,06 % de error medio, porque engorda las piezas finas. Por eso no
  manda en el mínimo (probado: ninguna regla de «% de área más fina que w aristas» da cifras parecidas en el humano y en el
  águila, y la que acierta en uno se pasa 20 veces en el otro).

Error medio previsto con el valor calibrado frente al medido en el Motor MBL (% de la diagonal; el medido a 1.000 caras
incluye piezas perdidas, que la curvatura no ve):

| Caras | Humano previsto / medido | Águila previsto / medido | Criatura previsto / medido |
|---|---|---|---|
| 1.000 | 0,375 / 0,338 | 0,473 / 1,322 | 0,473 / 0,978 |
| 2.500 | 0,178 / 0,373 | 0,269 / 0,231 | 0,269 / 0,203 |
| 5.000 | 0,096 / 0,131 | 0,170 / 0,115 | 0,169 / 0,135 |
| 12.000 | 0,044 / 0,046 | 0,090 / 0,067 | 0,090 / 0,059 |

Validación del mínimo (se ejecuta el Motor MBL con esa cifra y con la mitad; error medio / p99 / máximo, % de la diagonal):

| Modelo | Mínimo | Real con el mínimo | Real con la mitad |
|---|---|---|---|
| Humano | 9.176 | 0,056 / 0,78 / 1,32 | 0,146 / 1,42 / 2,26 |
| Águila | 17.687 | 0,056 / 0,98 / 2,38 | 0,082 / 1,42 / 3,38 |
| Criatura | 17.693 | 0,043 / 0,60 / 1,97 | 0,089 / 1,50 / 2,73 |

Con el mínimo, los tres quedan en verde con holgura (límite del semáforo: 0,25 % medio, 2,5 % máximo, aunque el águila roza el máximo);
con la mitad, el error medio sube hasta 2,6 veces y el p99 pasa de 1 % a 1,4 %. Es conservador en el error medio (0,04-0,06 %
reales frente a 0,12 % de objetivo) porque el margen se ajustó al p99. Pendiente: probar con Maya y con otros modelos.
