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
| Retopo | no-manifold; menos del 85 % de vértices regulares; error de forma medio > 0,6 % o máximo > 5 %; nota 2 del juez (agujeros nuevos; ≥ 0,5 % de caras dadas la vuelta o dobladas; ≥ 10 % de quads deformados; ≥ 15 % de saltos de tamaño; ≥ 5 % de triángulos o n-gons) | agujeros que el original no tiene; < 98 % quads; < 92 % de vértices regulares; > 0,25 % de polos de 6+ aristas; error medio > 0,25 % o máximo > 2,5 %; nota 1 del juez (≥ 0,05 % de caras dadas la vuelta o dobladas; ≥ 3 % de quads deformados; ≥ 5 % de saltos de tamaño; ≥ 1 % de triángulos o n-gons) |
| UVs | sin UVs o caras sin UVs; solapes; UVs en espejo; fuera del cuadro 0-1 (o islas cruzando UDIMs); < 35 % de textura aprovechada; > 20 % de caras estiradas; nota 2 del juez de UVs (≥ 0,5 % de caras solapadas o en espejo; ≥ 1 % sin UVs; estiramiento medio ≥ 1,6x; ≥ 30 % de caras de más de 1,5x; densidad de texel entre islas ≥ 60 %; ≤ 20 % de textura aprovechada) | < 50 % de textura aprovechada; > 10 % de caras estiradas más de 1,5x; nota 1 del juez de UVs (cualquier cara solapada, en espejo o sin UVs; estiramiento medio ≥ 1,3x; ≥ 12 % de caras de más de 1,5x; densidad de texel entre islas ≥ 25 %; ≤ 40 % de textura aprovechada, también con Costuras escondidas; margen entre islas ≤ 0,001 en UV; ≤ 0,1 % de los cortes que casan) |

Los umbrales salen de las pruebas con el humano y el águila: con los ajustes por defecto, los dos salen en verde.

Hay dos excepciones:
- Con **Costuras escondidas**, la textura aprovechada se mide con umbrales más bajos (35 % y 25 %), porque ese modo cambia resolución por costuras ocultas.
- En piezas de menos de 200 vértices no se avisa por polos: un cubo tiene 8 polos inevitables.

### Un solo juez de calidad (2 de noviembre)

El paso Retopo pasa ahora por el mismo juez que el banco de pruebas (`mbl_engine/quality.py`: `report()` y `grade()`), con sus mismos umbrales. Antes solo miraba forma, polos y quads y salía en verde con defectos que el juez sí veía (humano con Maya: 0,88 % de caras dadas la vuelta y 0,37 % dobladas, semáforo en verde).

- **Cómo.** `status.judge_report` pasa el retopo y su original a `Mesh` (`retopo.mesh_arrays`) y llama a `quality.report`. Cada cifra del juez sale como un aviso con su nivel (según `quality.LIMITS`) y su «→ qué tocar»: quads deformados, saltos de tamaño, caras dadas la vuelta y caras dobladas. Los triángulos se funden con el aviso de quads y los agujeros nuevos con el de agujeros (el recuento de Maya sigue avisando en amarillo; si el juez también los ve, rojo). El nivel del paso es el peor entre lo que ya calculaba y la nota del juez (0 verde, 1 amarillo, 2 rojo). Como red de seguridad, si `grade()` da más nota que todos los avisos juntos (por ejemplo, si algún día mira una cifra nueva), se añade un aviso con sus razones.
- **Sin original o con uno enorme.** Sin original, o con más de 1.000.000 de triángulos (`JUDGE_MAX_SOURCE_TRIANGLES`: convertirlo y construir su rejilla cuesta tiempo y memoria), no se compara con él: se miden solo las cifras que no lo necesitan (quads deformados, saltos de tamaño, caras dobladas, triángulos) y, si el original existe, sale un aviso amarillo diciendo que no se comprueban las caras dadas la vuelta. Si el cálculo del juez falla, el paso sale en amarillo con la traza en el Script Editor.
- **Las UVs y la limpieza no cambian.**

Banco (`tests/bench.py`, 5.000 caras, nota del juez y paso Retopo del semáforo antes → después; la geometría es la misma que en la línea base):

| Modelo / motor | Nota del juez | Semáforo Retopo |
|---|---|---|
| criatura / mbl | 2 | amarillo → rojo |
| hardsurface / maya | 1 | verde → amarillo |
| hardsurface / mbl | 0 | verde → verde |
| human / maya | 2 | verde → rojo |
| human / mbl | 2 | amarillo → rojo |
| prop / maya | 2 | amarillo → rojo |
| prop / mbl | 2 | verde → rojo |

Midnight no se puede medir en el banco (falla antes del semáforo) y criatura / maya tampoco. Tiempo del cálculo del juez: humano (original de 28.092 triángulos, retopo de 5.095 caras) 0,2 s, y `retopo_status` entero 0,3 s; criatura (original de 528.554 triángulos, retopo de prueba de 5.352 caras reducido con `polyReduce`) 1,7 s (0,5 s en convertir el original y 1,2 s en `quality.report`, que incluye su rejilla y el recuento de bordes), y `retopo_status` entero 2,4 s (6,6 s la primera vez, por las caras recomendadas, que ya se calculaban antes).

Los consejos «→ qué tocar» de caras dadas la vuelta y dobladas (más caras, otro motor, *Pintar detalle* en dedos que se tocan) están redactados por criterio y por la guía del tutorial; no se ha medido que arreglen esos defectos.

## Parámetros

**Limpieza**
- *Fusionar vértices*: une los vértices que estén más cerca que esa distancia (en unidades de la escena, normalmente cm). Las costuras UV que ya tuviera la malla se conservan.

**Retopología**
- *Motor*:
  - **Automático**: hace el retopo con Maya y con el Motor MBL y se queda con el de menor coste (forma, polos y caras que no son quads; ver *En el plugin (30/10)* más abajo). El log muestra las cifras de los dos y cuál se ha elegido. Tarda lo que los dos juntos (de 30 s a 2 min). Cuando el Motor MBL no entra (guías de flujo, detalle pintado o sin QuadriFlow) es solo Maya.
    - Hasta la versión 7 de los ajustes era el motor por defecto; en la 8 pasó a ser Maya (ver «El motor por defecto pasa a Maya y la ventana se ordena»). Si lo tenías guardado de una versión anterior pasas a Maya una vez; después se respeta lo que elijas, también Automático.
    - Los ajustes guardados con el motor por parches que se quitó (ver «Motores descartados») también pasan al motor por defecto (Automático en la versión 7, Maya desde la 8).
  - **Maya (polyRetopo)** (por defecto desde la versión 8 de los ajustes): el más rápido de los buenos.
    - Va con *regularidad de topología* 1 (Maya trae 0,5). Se eligió con un barrido de sus parámetros:
      - **Humano a 2.000 caras:** polos de 157 a 142 y error medio del 0,207 % al 0,195 %. Las aristas en loops cerrados suben del 15 % al 25 %.
      - **Águila a 5.000 caras:** polos de 593 a 560 y polos de 6+ de 20 a 13.
      - Anisotropía, uniformidad de caras y desviación de aristas no mejoraron nada.
    - **Agujeros nuevos:** polyRetopo abre agujeros en zonas finas, como 5 en las plumas del águila. Unfold3D los ve como UVs no-manifold. El plugin compara los bordes del resultado con los del original y cierra con quads solo los que el original no tenía. Las aberturas reales del modelo, como cuello o mangas, se dejan.
  - **Instant Meshes** es el más rápido, con opciones propias (suavizado, solo quads, determinista, ángulo de aristas duras, densidad por curvatura) que no afectan a los demás motores.
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
- *Ángulo aristas duras*: con Instant Meshes, las aristas con un ángulo mayor se respetan como bordes marcados. Con Maya y el Motor MBL (y, por tanto, en Automático) solo cambia el sombreado del resultado (aristas duras o suaves). Con 0 se desactiva, que es lo habitual en mallas orgánicas.
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
- *Resolución textura* y *Padding*: el padding se indica en píxeles a esa resolución. Con 0 (el valor por defecto) es automático: 1/256 del lado de la textura, es decir 8 px a 2K, 16 px a 4K y 32 px a 8K (mínimo 2 px), así a 1/8 de la resolución (mipmaps) las islas siguen separadas por 1 px. 16 px a 4K es un valor seguro para Substance. Quien tenía el 16 antiguo pasa a automático (versión 8 de los ajustes); cualquier otro número se respeta.
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


**En el plugin (30/10).** El desplegable *Motor* tiene **Motor MBL (solo quads)** y el modo **Automático** lo incluye: hace el retopo de Maya (con su simetría) y el Motor MBL, y se queda el de menor coste (forma, polos y caras que no son quads). Entonces el lado clásico era el mejor de Maya y de un motor por parches, que se retiró el 2 de noviembre (ver «Motores descartados»). El Motor MBL hace siempre el modelo entero, no admite guías ni detalle pintado (en ese caso se usa el motor clásico) y usa `mbl_variants` (3) y `mbl_time_budget` (270 s) de los ajustes. QuadriFlow viene en `mbl_retopo_uv/bin/quadriflow.exe`. Sin él, el automático usa solo Maya.

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
- **Aviso de piezas finas** (`THIN_NOTE_PERCENT`, 20 %). Aunque se pida más que el mínimo, el log y *Recomendar caras*
  avisan si con las caras pedidas más del 20 % del área de volúmenes queda más fino que 3 aristas (dedos que se tocan,
  ropa pegada al cuerpo, pliegues). Área fina a 30.000 caras: caja dura 0,3 %, humano 6 %, águila 18 %; un personaje de
  Meshy 36 %, la criatura 50 % (con mínimos por curvatura de 26.900 y 17.700, así que el aviso de «pides menos del
  mínimo» no salta). El umbral se puso entre los modelos que dieron buen resultado (humano, águila) y los de mucha más
  superficie fina. Mide superficie, no cuántas piezas salen mal: no se ha contado dedo a dedo.

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

## Memoria del Motor MBL (2 de noviembre)

Incidente: con 40.000 caras objetivo, el Motor MBL lanzó tres QuadriFlow a la vez con `-f 520000` y dejó el PC (15,7 GB) con
0,8 GB libres. Causas: la primera pasada pidió 52.000 caras, QuadriFlow devolvió unas 10 veces menos, `_next_request` multiplicó el
pedido por objetivo/obtenido sin tope (520.000) y `resources.safe_workers` calculaba la memoria solo con las caras de entrada.

Memoria máxima de QuadriFlow medida (malla de entrada de 100.000 triángulos, Windows): 5.000 caras pedidas, 0,39 GB; 25.000, 1,46 GB;
60.000, 3,32 GB. Sube con las caras **pedidas**, unos 54 KB cada una (la medición de 150.000 se detuvo a propósito: habría gastado ~8 GB).

Qué se hizo (`mbl_engine/retopo.py`, `resources.py`, `process.py`):

- **Tope del pedido:** cada corrección pide como mucho el doble (o la mitad) que la anterior y nunca más de 1,5 veces el objetivo. Si lo
  que da QuadriFlow se desvía más de 3 veces del objetivo, la variante se descarta como fallo del programa.
- **Presupuesto de memoria:** `job_memory` usa las caras pedidas (60 KB por cara, 3 KB por triángulo de entrada, 100 MB fijos). Todos los
  motores juntos no usan más de lo libre menos 2 GB de margen ni más de la mitad de la memoria total. Si ni una variante cabe, el Motor MBL
  se niega con un mensaje que dice cuántas caras se pueden pedir; en modo Automático se descarta y se usa Maya.
- **Un proceso a la vez** en equipos con 16 GB o menos (`SMALL_RAM_BYTES`); hasta 3 en equipos grandes si cabe.
- **Antes de lanzar** cada variante se espera a que haya memoria (si otras corren y van a liberar); si no hay ninguna corriendo y no
  cabe, no se lanza.
- **Vigilancia mientras corre:** cada 0,5 s se mira la memoria libre; si baja de 1,2 GB se detiene el proceso más nuevo (si es el único,
  ese) con un aviso, y las demás variantes siguen o el retopo termina con error, pero Maya no se cuelga.
- **Prioridad baja** (`BELOW_NORMAL_PRIORITY_CLASS`) para los procesos de los motores.
- **Aviso** en el log si se piden más de 100.000 caras.
- No se limitaron los hilos de QuadriFlow: haría falta una opción en nuestra copia del programa. Pendiente.

Pruebas: `tests/test_safety.py` (sin Maya ni modelos; se simula QuadriFlow): el pedido nunca pasa de 1,5 veces el objetivo aunque devuelva
10 veces menos, la variante mala se descarta, una malla que no cabe en memoria no lanza ningún motor y la vigilancia detiene un proceso
en menos de unos segundos sin dejar nada colgado.

## Retopo sin no-manifold (2 de noviembre)

Línea base (5.000 caras, ajustes por defecto): `criatura` y `midnight` con Maya se negaban a desplegar UVs («1 aristas y 2 vértices
no-manifold») y `midnight` con el Motor MBL fallaba en Unfold3D («Mesh has non-manifold UVs»). Se midió la topología tras cada etapa
(salida de polyRetopo, `orient_pieces_like`, `collapse_tiny_edges`, `fix_nonmanifold`, `optimize_quads`) y salieron dos causas:

- **Aletas tras `collapse_tiny_edges` (Maya).** `polyCollapseEdge` de una cara diminuta dejó, en `criatura` y `midnight`, dos triángulos con
  los mismos vértices y orientación contraria pegados a una arista de la superficie. Para Maya son aristas distintas y `polyInfo` da
  `(0, 0)`; al reconstruir la malla desde sus caras (`optimize_quads` -> `create_mesh`) la arista queda con 4 caras (1 arista y 2
  vértices no-manifold) y además aparece un vértice de 2 aristas.
- **Quads con la diagonal repetida (Maya y Motor MBL).** Un quad cuyas esquinas opuestas ya están unidas por una arista de otra cara (un
  triángulo de aristas que no es una cara). `midnight` tenía 1 con Maya (ya en la salida directa de polyRetopo) y 2 con el Motor MBL;
  `human`, `prop`, `hardsurface` y `criatura` con Maya, ninguno. Unfold3D triangula el quad por esa diagonal y deja la arista en tres
  caras: «non-manifold UVs», aunque `polyInfo` (mallas y UVs) no marque nada y `fix_nonmanifold_uvs` no encuentre nada. Reducido por
  bisección de caras a 4 quads (8 vértices); partiendo los 2 quads por la otra diagonal, las UVs de `midnight` con el Motor MBL salen.

Arreglo (`mbl_engine/repair.py: make_manifold`): quita las aletas (cae la cara con menos aristas compartidas, no «la de después»), disuelve
vértices de 2 aristas, separa pajaritas y parte los quads con diagonal repetida en dos triángulos por la otra diagonal (un vértice en el
centro si las dos lo son o la cara tiene más de 4 lados). Si no hay nada devuelve las mismas listas. Se aplica al final de `retopo()` del
núcleo (todas las variantes del Motor MBL, también tras soldar la simetría) y en `_finish` / `_symmetric_retopo` del adaptador (después
de `optimize_quads`, antes de reproyectar). Cuesta 2 triángulos por quad partido.

Resultado con el banco: `midnight` con Maya (5.355 caras, 258 polos, error medio 0,138 %) y con el Motor MBL (5.004 caras, 368 polos, 0,101 %) y `criatura` con Maya (5.721 caras, 363 polos, 0,129 %) completan Limpiar + Retopo + UVs; `human`, `prop` y `hardsurface` con Maya dan las
mismas caras, polos, fealdad y error que la línea base. Pendiente: una comprobación previa en `uv.unwrap` que explique este fallo en mallas
del usuario que no pasan por el retopo (hoy sale el mensaje de Unfold3D tal cual).

## Mapa de coste de polos para animación (2 de noviembre): probado y descartado

Idea: un valor por punto del original (alto en articulaciones y cara, sacado del skin o de un pincel) que pondera la energía de
`quadopt.clean_poles`: cada vértice cuesta `coste * (aristas - 4)^2` en vez de `(aristas - 4)^2`, y los giros de arista y colapsos
empujan los polos hacia las zonas baratas. Se implementó (energía ponderada en giros, colapsos, giros generales y uniones, mapa
posición -> coste con interpolación sobre el original, ensanchado del mapa sobre la malla del retopo y recuento de polos en zona)
y se midió en el humano a 5.000 caras, con el mapa en bolas alrededor de polos y en cabeza, mano y pie. **No funciona**, así que no
se ha incluido en el plugin:

| Prueba | Sin mapa | Con mapa |
|---|---|---|
| Polos dentro de la zona cara (3 bolas de 3 aristas, 8 ensayos), coste ×3 / ×10 / ×30 | 19,1 | 21,4 / 21,6 / 21,5 |
| Lo mismo con bolas de 1,5 aristas | 9,2 | 9,9 / 9,8 / 9,9 |
| Búsqueda de profundidad 5 en vez de 3 (coste ×30) | 19,2 | 21,7 |
| Retopo completo, 6 semillas, zonas en cabeza, mano y pie (coste ×10): polos en zona | 55, 68, 44, 61, 47, 57 | 56, 66, 46, 65, 47, 58 |
| Polos totales en esas 6 semillas (media) | 147 | 156 |

Por qué: los polos que quedan tras la limpieza normal (140 de 198 en este retopo) no tienen pareja a la que anular a unas pocas
operaciones. Un giro de arista no mueve un polo suelto: cambia a la vez cuatro vértices (dos bajan y dos suben una arista), así que un
5 solo no se puede desplazar. Con la energía ponderada solo se pueden mover parejas 3-5, que ya se anulaban antes; los polos
aislados siguen donde los dejó QuadriFlow, y el mapa solo añade polos (de 147 a 156). Subir la profundidad de la búsqueda a 5 no
cambia el resultado y cuesta 12 veces más.

Lo que sí varía mucho es la semilla de QuadriFlow: los polos en zona van de 44 a 68. Pero quedarse con la variante de menos polos en
zona casi coincide con lo que el motor ya hace (la de menos polos en total, la de la semilla 3, también tiene menos en zona), así que
ponderar la elección de variantes con el mapa añadiría poco.

Para controlar de verdad dónde quedan los polos hace falta un campo de direcciones propio con singularidades prescritas (por
ejemplo conexiones triviales, Crane 2010) o forzar bucles con las guías de flujo (motor de Maya). Con el estado actual, para animación
sirven más las **guías de flujo** alrededor de las articulaciones y la cara, que el motor de Maya sigue, que un mapa de coste.

## Optimizar el Motor MBL para equipos modestos (3 de noviembre): lo que se midió y por qué no llegó al −30 %

Medido con `tests/bench.py --engines mbl` (5.000 caras, una medición cada una, PC de 16 núcleos y 15,7 GB sin otra carga; «main» y «optimizada» se midieron seguidas) y con un perfilador propio por etapas. Tiempos del paso de retopo, en segundos:

| Modelo | línea base (baseline.json) | main | optimizada | pico de memoria main -> optimizada (MB) |
|---|---|---|---|---|
| humano | 29,3 | 21,1 | 17,3 (−18 %) | 1.229 -> 1.227 |
| prop | 56,1 | 40,4 | 35,3 (−13 %) | 1.340 -> 1.342 |
| hardsurface | 56,3 | 25,0 | 22,7 (−9 %) | 1.301 -> 1.298 |
| criatura | 127,7 | 56,1 | 45,4 (−19 %) | 3.325 -> 3.298 |

La línea base de `baseline.json` se midió con el PC ocupado; con el PC libre el paso ya tarda lo de la columna «main». El criterio (−30 % en 3 de 4 y memoria −15 % en la criatura) no se alcanza con cambios que dejen el resultado igual.

**Dónde está el tiempo** (humano, sin perfilador de por medio: cProfile multiplica por ~7 el coste de las funciones recursivas y hacía creer que `clean_poles` costaba 1,4 s por variante; son 0,2 s):
- QuadriFlow es la mayor parte: 12,7 s de 21 en el humano (3 ejecuciones de ~4 s), y ~80 % en hardsurface. Su tiempo depende de las caras PEDIDAS (la fase «index map» es el 55 %), no de la malla de entrada: el humano con 28.000, 26.000 y 13.000 triángulos de entrada tarda 3,69, 3,75 y 3,57 s. Por eso reducir la malla que ve el motor (`DECIMATE_TO`) no ahorra tiempo, y la memoria que ahorraría (~70 MB en la criatura) es poca.
- Tres de los cuatro modelos gastan DOS ejecuciones de QuadriFlow por variante: la primera pasada (6.500 pedidas) da 6.400 caras en prop y hardsurface, 5.600-5.900 en la criatura, fuera de la tolerancia del 12 %, y hay que repetir. Solo el humano (4.700-5.400) acierta a la primera. Seis ejecuciones de QuadriFlow son 25 s de 35 en prop y 20 s de 25 en hardsurface.
- QuadriFlow no se puede ejecutar a la vez en dos variantes sin pasar del pico de memoria de la línea base (cada proceso 0,36-0,54 GB).
- La criatura, además, tarda en Python (~5 s por variante) y en `prepare` (~7 s) por sus 528.000 triángulos.

**Memoria de la criatura**: el pico de 3,3 GB es mayapy 2,85 GB + QuadriFlow 0,43 GB + mbl_geom 0,04 GB. Antes de que el motor haga nada, mayapy ya está en 2,5 GB: 1,2 GB al abrir el modelo y +1,3 GB durante la limpieza (todo el pipeline va en un solo undo chunk y Maya guarda cada operación sobre la malla de medio millón de triángulos). La parte del motor es ~0,3 GB; con lo que se ha quitado queda en ~0,25. Bajar un 15 % (500 MB) pide actuar sobre la limpieza de Maya (`mbl_retopo_uv/pipeline.py`, fuera de esta tarea).

**Qué se cambió (mismo resultado en humano, hardsurface y criatura; prop, ver más abajo)**:
- `retopo()` se parte en `run_engine()` (motor) y `finish_retopo()` (Python): `best_retopo()` hace la segunda mitad de una variante en otro hilo mientras QuadriFlow ya trabaja en la siguiente. Es lo que más ahorra (−10-20 %).
- `prepare()` sin listas de listas de Python para la malla (`compact_mesh`, `drop_small_pieces` en numpy, triángulos como array) y el original se comparte con la malla de trabajo cuando no se quitó nada: ~100-200 MB menos de objetos temporales y ~2,5 s menos en la criatura.
- Caché de vecinos en `quadopt._Mesh`, sin contar polos si nadie lee el mensaje y `process.wait(timeout)` en vez de dormir 0,1 s de más.
- Pruebas nuevas: `drop_small_pieces` frente a la implementación de referencia con mallas al azar, y el reparto de la petición entre variantes.

**Probado y apagado: `LEARN_QUADRIFLOW_REQUEST`** (o `options["learn_request"]`). La primera variante cuenta lo que le hizo falta y las demás empiezan con esa petición: una ejecución menos por variante. Con bench (retopo, sobre «main»): prop −44 %, hardsurface −41 %, humano −18 % (no cambia), criatura −7 % (ahí manda el Python). Pero las variantes 2 y 3 salen distintas (otra muestra del motor caótico) y la calidad deja de cumplir: prop error medio 0,082 -> 0,114 %, hardsurface loops cerrados 16 -> 4,7 %, criatura fealdad 4,7 -> 8,4. Por eso va apagado.

**Un aviso sobre el determinismo**: QuadriFlow SÍ es determinista (misma entrada y petición: 8 de 8 salidas idénticas, también con 16 procesos ocupando la CPU). Pero la variante 1 del prop no es reproducible entre ejecuciones del pipeline, tampoco con el código sin tocar: 4.793 caras/97 polos, 4.800/200, 4.788/116 (error 2,45 %) y 4.732/436 en cuatro ejecuciones; las variantes 2 y 3 sí salen siempre iguales. Algo anterior al motor (¿el orden de vértices que deja la separación de no-manifold de Maya?) cambia la entrada. Sin resolver; cuando pasa, gana otra variante y el prop sale con error 0,150 % en vez de 0,082 %.

## El _retopo conserva materiales, pivote, transformación y grupo (3 de noviembre)

`meshbuild.create_mesh` crea la malla en la raíz, sin transformación y con el pivote en el origen, con los puntos en mundo. Casi todos
los pasos del retopo rehacen la malla así (agujeros nuevos, limpieza de polos y relajación, `make_unfoldable`, Motor MBL, Instant
Meshes, simetría), de modo que el `_retopo` salía fuera del grupo, sin el movimiento ni el pivote del original y con un solo material
(el primero que devolvía `listConnections`). Con el modelo de prueba (esfera de 384 caras, grupo girado, trasladada/girada/escalada,
pivote movido, dos materiales) y el motor de Maya el `_retopo` salía en `|vestida_retopo`, pivote (0,0,0) y todo azul.

`_present` llama ahora a dos funciones de `retopo.py`:

- `_match_placement(original, nuevo)`: si grupo, atributos del transform y pivotes ya coinciden (motor de Maya sin rehacer la malla) no
  hace nada. Si no: lleva la malla a la raíz y congela (puntos en mundo), la multiplica por la inversa de la matriz de mundo del original
  (`xform -matrix` + `makeIdentity`), la mete en el grupo a continuación del original y copia `rotateOrder`, `translate`, `rotate`,
  `scale`, `shear`, `rotateAxis` y los cuatro pivotes. Solo comandos de `cmds`: un Ctrl+Z lo deshace con el resto. `MFnMesh.setPoints`
  no sirve porque no entra en el deshacer. Si el original tiene escala negativa se invierten las caras (el sentido se da la vuelta al
  pasar al espacio de objeto). Comprueba al final que ningún vértice se movió en mundo y avisa si no.
- `_copy_materials(original, nuevo)`: `MFnMesh.getConnectedShaders` del original y, con 2 o más materiales, cada cara del retopo toma el
  de la cara más cercana del original (`MMeshIntersector`, `.face`); el más repetido va al objeto entero y el resto por caras.

Comprobado en mayapy con `maya`, `instant_meshes` (sustituto), `mbl`, `auto` y simetría `x`: grupo, pivotes, caja envolvente (menos del
2 % de la diagonal) y materiales (más del 95 % de las caras de cada uno en su zona), también con shear, orden de giro distinto, pivotes
de escala, grupos anidados con escala no uniforme y sin grupo. Sin cambios en caras, polos ni error del banco (human, `maya`: 5.095
caras, 174 polos, igual que `baseline.json`).

Hallazgo fuera de esta tarea: **Rehacer (Ctrl+Y) tras deshacer el comando `mblCreateMesh` cierra Maya** (fallo fatal en `redoIt`; pasa con
cualquier `create_mesh` en mayapy, también en la rama base, sin tocar nada de esto). Deshacer funciona bien. Y `baseline.json` guarda
`status.retopo = 0` para human/maya, de antes del juez en el semáforo; con el código actual (también en 4020e31 sin esta rama) sale 2.

## Tamaño de trabajo de los motores y aristas duras de los escaneos (3 de noviembre)

**Escala.** Los motores tienen números absolutos y el mismo modelo en centímetros, en metros o en milímetros no sale igual. Medido
(sin pasar por el resto del proceso, `retopo.retopo` con el motor de Maya y el núcleo con los motores reales):

| motor | modelo y escala | resultado antes |
|---|---|---|
| polyRetopo, 400 pedidas | toro regular, diagonal 1,1e5 | 3,4 s en vez de 0,3 s |
| polyRetopo | el mismo toro, diagonal 1,1e6 | no termina (más de 5 minutos) |
| polyRetopo | el mismo toro, diagonal 1,1e-4 | el resultado sale con el 45 % del tamaño del original |
| polyRetopo, 800 pedidas | esfera con ondas, diagonal 1,2e5 | 1.031 caras, 104 polos y 10 s (con tamaño normal, 22 polos y 1 s) |
| polyRetopo | la misma esfera, diagonal 1,2e-5 | el resultado se queda en un punto (1 % del tamaño) |
| núcleo, QuadriFlow, 600 pedidas | esfera de diagonal 8 por 1, 0,01, 100, 0,0001 y 10.000 | 574, 621, 648, 617 y 696 caras |
| núcleo, Instant Meshes, 600 pedidas | la misma | 628, 598, 567, 610 y 644 caras; 22 a 32 polos |

Entre diagonales de 1e-3 y 1e4 polyRetopo no depende de la escala: lo que parecía un efecto de las unidades (esfera con ondas: 774,
842 y 882 caras a x1, x0,01 y x100) era ruido, ver «Lo que no se arregla». Lo que sí falla son los extremos de la tabla. En el
núcleo el OBJ que se escribe a los motores lleva 6 decimales fijos (`repair.write_obj`), un número absoluto que a escalas pequeñas
pierde precisión; polyRetopo tiene tolerancias absolutas (`shortEdgeTolerance`, 0,0001), que es lo más probable que se note en los
extremos, pero no lo he aislado.

Arreglo: `mbl_engine/scale.py` (función pura, sin Maya). Antes del motor el modelo se multiplica por una potencia de 10 que lleva su
diagonal a [30, 300) y al terminar se devuelve a su tamaño. Con la diagonal ya dentro de ese rango no se toca nada (identidad: ni
siquiera se copian los puntos). El rango son justo 10 veces, así que el mismo modelo en cm, m o mm (cualquier relación de potencia de 10)
llega al motor con exactamente el mismo tamaño; con un rango más ancho habría tamaños que se quedan como están mientras sus versiones
x10 se llevan a otro sitio. La malla que se retopologiza en el banco, ya limpia (el transform congelado), mide: hardsurface 38,4
(su caja de todas las piezas, 1.352, no es la que entra al motor), prop 55,6, midnight 247,1, criatura 251,8 y human 257,6: las cinco
caen dentro y dan lo de siempre. Un primer rango [50, 2.000) cambiaba el hardsurface (38,4 quedaba fuera: 4.738 caras pasaron a 5.056).

- Núcleo: decorador `@at_work_scale` en `retopo()` y `best_retopo()` de `mbl_engine/retopo.py` (tres líneas). Los dos entran por el
  mismo sitio, así que las variantes, la referencia de error y el ajuste final ven siempre la misma escala; anidados (`best_retopo`
  llama a `retopo`) la segunda vez no hacen nada. El informe lleva `scale` solo si hubo cambio.
- Maya: `_maya_engine` mide la diagonal en el espacio de objeto (`MFnMesh.getPoints(kObject)`: `polyEvaluate -boundingBox` y
  `xform -bb -os` incluyen el giro del transform y dan 18,1 en vez de 12,3 para el mismo modelo) y multiplica los puntos de la copia de
  trabajo antes de `polyRetopo`; al terminar, los devuelve. Lo posterior (reproyección, optimización de cuadrados, simetría) ya se hace
  con el modelo a su tamaño.
- Después, en el núcleo con la misma esfera: QuadriFlow 648 caras y 8 polos en las cinco escalas; Instant Meshes 567 caras y 32 polos
  en las cinco. En Maya (toro regular a x1, x0,01, x100 y x10.000): 392 caras y 0 polos las cuatro veces, y polyRetopo ve siempre la
  diagonal 114,9. Con diagonales de 1e-3 a 1e7 sale lo mismo en 0,3 s; antes x1e5 no terminaba. Banco con `maya` (human, prop,
  hardsurface): cero diferencias con `baseline.json` fuera de `status` y tiempos.

**Lo que no se arregla.** (1) polyRetopo reacciona al ruido de float: con la esfera con ondas y los puntos con un ruido relativo de
1e-7 (el de un float32) salen 850 o 913 caras (2 de cada 8 veces); con 1e-6, entre 790 y 907; incluso el toro regular da 406 caras
en vez de 392 con unos puntos float32 que difieren en el último bit. Un modelo en cm y el mismo en m no tienen los mismos puntos
float32, así que en Maya la igualdad exacta de caras entre unidades no se puede garantizar para cualquier malla con ninguna
normalización (en el núcleo sí: la entrada es de doble precisión y llega idéntica al OBJ). Las pruebas construyen las mallas con
`meshbuild.create_mesh` y no con `setPoints` sobre una primitiva, que deja un tweak de ~1e-7 absoluto (a escala 0,001 ya es el 3e-5 del
tamaño y cambia el resultado). (2) Por debajo de una diagonal de ~1e-4 el resultado sigue saliendo deformado (tamaño 0,69 a x1e-5 y
0 a x1e-6 en el toro) aunque polyRetopo ya trabaje a escala normal; no he localizado qué paso lo estropea y son tamaños sin uso real.
(3) El motor de Instant Meshes del adaptador (`_instant_meshes_engine`, solo si se elige a mano) no se normaliza: exporta con
`cmds.file` y habría que saber en qué espacio escribe el OBJ. (4) `cleanup_merge_distance` (0,001, en unidades de la escena) es un
umbral absoluto del paso de limpieza: un modelo de 1 cm en una escena en metros tiene, a 5.000 caras, aristas de unos 0,0004 y la
fusión de vértices lo destruiría. No se toca (es un ajuste del usuario con otro significado), pero conviene avisar o hacerlo relativo.

**«Todo duro» de los escaneos.** El plugin no usa las marcas de dureza (normales duras) del modelo en ningún sitio del retopo:
`polyRetopo` se llama sin `preserveHardEdges` (apagado por defecto; es el ajuste que, según el informe de investigación, se confunde
con mallas con todas las aristas duras), Instant Meshes recibe un OBJ exportado con `smoothing=0;normals=0` y su `-c` es un ángulo
diedro que calcula él con la geometría, `uv.hard_angle_edges` calcula el ángulo entre normales de cara (geométrico, como
`polySelectConstraint`) y `_present` solo escribe `polySoftEdge` en el resultado. Medido: una esfera con ondas con todas las aristas
duras (`polySoftEdge -angle 0`, 100 % duras) y con todas suaves (0 %) da exactamente los mismos puntos, caras y polos. Por eso no
hay nada que detectar ni ignorar; la prueba `test_all_hard_edges_do_not_change_the_retopo` lo fija para que, si algún día se activa
`preserveHardEdges`, se vea que hay que ignorar las marcas cuando pasan del 50 % de las aristas.

## Motores descartados

Motores que se probaron, se midieron y se quitaron del plugin. Las cifras se guardan aquí por si hay que volver a decidir.

### QuadWild-BiMDF (retirado el 2 de noviembre)

[QuadWild-BiMDF](https://github.com/cgg-bern/quadwild-bimdf) (Universidad de Berna, licencia GPL-3, versión 0.0.2) era el motor «por parches»: divide el modelo en parches y reparte los quads en cada uno resolviendo un problema de flujo que minimiza los polos. El plugin ejecutaba sus binarios oficiales sin modificar (`quadwild.exe` y `quad_from_patches.exe`, en `mbl_retopo_uv/bin/quadwild`) como programa aparte, igual que Instant Meshes.

**Por qué se quitó.** Deja agujeros en las mallas sucias (el águila) y solo gana a Maya con muchas caras en mallas limpias. Eso no compensa llevar y mantener un programa GPL-3 dentro del plugin, con su aviso de licencia y su código fuente aparte. Ahora el modo Automático es el mejor entre Maya y el Motor MBL, y el *Ángulo aristas duras* lo usa solo Instant Meshes. El código y los binarios siguen en el historial de git (hasta el commit `7f1fc8b`). Los ajustes guardados con `retopo_engine = "quadwild"` pasan a `"auto"` al cargarse (versión 7 de los ajustes, `config.REPLACED_VALUES`).

**Cómo funcionaba:**
1. `quadwild.exe <malla> 2 <prep.txt>` remallaba, calculaba el campo de cruces y trazaba los parches (`<malla>_rem_p0.obj`). Ajustes: `do_remesh 1`, `scaleFact 1`, `sharp_feature_thr` con el ángulo de aristas duras (o -1 sin él) y `alpha` 0,02 sin aristas duras (configuración *Organic*) o 0,01 con ellas (*Mechanical*).
2. `quad_from_patches.exe <malla>_rem_p0.obj 1 <main.txt>` repartía los quads en cada parche (configuración `flow.txt` con `flow_virtual_simple.json` y Satsuma `default.json`, `alignSingularities` activo, límite de 200 s). El tamaño de quad es la arista media de `_rem_p0` por `scaleFact`: el número de caras se controla con `scaleFact`.
3. **Suavizado previo:** el campo sigue la curvatura y el ruido fino crea singularidades (= polos). Se suavizaba la copia 10 pasadas antes de pasarla: humano a unas 8.000 caras, de 292 a 244 polos.
4. **Número de caras:** `scaleFact` inicial = raíz(área / caras pedidas) / arista media, por 1,4 (salían unas 2 veces las del área: humano 1,8, águila 2,4). Hasta 3 intentos, corrigiendo con el exponente medido entre los dos últimos (las caras no van con 1/scale²: en el humano, unos 0,8). Se quedaba el intento más cercano al objetivo.
5. **Después:** la misma reparación de topología, limpieza de polos, relajación y reproyección que los otros motores. Si quedaban más agujeros que en el original, se descartaba y se usaba Maya.
6. **Detalles de Windows:** `quad_from_patches` 0.0.2 termina con el código 0x80000003 al cerrarse, ya con la malla guardada (se aceptaba si el archivo existía), y se vigilaba el bloqueo con 120 s sin CPU en lugar de los 20 s de Instant Meshes.

**Modo Automático con QuadWild.** Hacía el retopo con Maya y con QuadWild y se quedaba con el que tuviera menos polos por cada 100 caras, sin agujeros. Para elegir QuadWild exigía al menos un 5 % menos de polos por cara (`AUTO_MIN_POLE_GAIN`) y como mucho el doble de error de forma medio (`AUTO_MAX_ERROR_RATIO`). Tardaba lo que los dos juntos (de 30 s a 1,5 min).
- Humano a 3.000 caras: elegía Maya.
- Humano a 8.000 caras: elegía QuadWild, con 217 polos frente a 319.
- Águila: elegía Maya, porque QuadWild dejaba agujeros.

**Resultados medidos** (polos, y entre paréntesis polos por cada 100 caras; menos es mejor):

| Malla | Caras | Maya | QuadWild |
|---|---|---|---|
| Humano | ~3.000 | 159 polos (5,5) | 234 polos (6,8) |
| Humano | ~8.000 | 319 polos (3,7), error 0,038 % | **217 polos (2,9)**, 97 % de vértices regulares, error 0,061 % |
| Águila (IA, 528k) | 5.000–9.000 | 372–518 polos | deja agujeros: se usaba Maya |

- **Polos:** en QuadWild dependen casi solo de la forma del modelo, no del número de caras. Por eso compensaba con muchas caras y en mallas limpias, y perdía con pocas.
- **Agujeros:** con mallas sucias (el águila) algunos parches no se cuadraban y quedaban agujeros que el original no tiene. Rellenarlos metía n-gons y polos de 6+ aristas y el error de forma se triplicaba: error máximo 5,2 % y 0,5 % de polos de 6+ (el semáforo del retopo avisa de ese caso).
- **Tiempo:** entre 20 y 90 s.

## Tiempo del paso de UVs (3 de noviembre)

**Dónde se iba el tiempo.** Perfil (`cProfile` con mayapy, retopo de Maya a 5.000 caras, humano / prop / hardsurface). Dos tercios del paso
no eran Maya sino Python puro de `uv_seams.py` (las costuras de sastre):

| | humano | prop | hardsurface |
|---|---|---|---|
| paso de UVs con el perfilador | 32,8 s | 23,4 s | 8,9 s |
| comandos de Unfold3D (`u3dUnfold`, `u3dOptimize`, `u3dLayout`, `u3dAutoSeam`) | 7,0 s | 7,1 s | 3,8 s |
| `optimize_seams` (Python) | 22,4 s | 14,1 s | 1,0 s |
| de ello `charts()` (union-find, 838 llamadas en el humano) | 11,3 s | 5,9 s | |
| de ello `all_disks()` (135 llamadas) | 7,5 s | 4,9 s | |
| de ello Dijkstra `_paths()` (345 llamadas) | 3,1 s | 1,6 s | |
| rayos de visibilidad (`anyIntersection`, 2 grafos en el humano) | 2,6 s | 2,4 s | 1,0 s |

**Qué se cambió (mismo resultado):** las UVs salen iguales que antes en humano y hardsurface (idénticas bit a bit) y en el prop (solo
difieren en el sexto decimal; Unfold3D ya no repite a ese nivel ni consigo mismo).

- `charts()` y `all_disks()` con numpy: componentes por salto de punteros; la característica de Euler se saca de tablas
  (esquinas, enlaces entre caras, agujeros) y no recorriendo caras y vértices. Resultado guardado por conjunto de costuras.
- `reroute_slits()` mide las islas una vez por vuelta (las costuras no cambian dentro de ella) y arma las máscaras con numpy.
- `_paths()` para al terminar la capa del primer destino alcanzado: distancias y camino de los destinos son los del recorrido completo
  (costes positivos), y quien llama solo mira los destinos.
- Un solo `SeamGraph` por desplegado (antes 5-6): la visibilidad por rayos y el espejo se calculan una vez.
- `uv_seam_edges` (con el grafo) y `_face_shell_areas` con numpy.
- Pruebas: `test_uv_seam_graph_matches_reference` y `test_uv_seam_edges_and_areas_match_reference` comparan con los recorridos de siempre.

**Medidas** (`tests/bench.py`, motor `maya`, 5.000 caras, segundos del paso de UVs; misma sesión, una detrás de otra):

| modelo | línea base | `main` ahora | esta rama | calidad |
|---|---|---|---|---|
| humano | 32,8 | 19,0 | 9,5 (-50 %) | igual (28 islas, 60,3 % usado, 3,5 % > 1,5x, racord 27,5) |
| prop | 16,5 | 13,2 | 8,9 (-33 %) | igual |
| hardsurface | 6,8 | 6,9 | 5,3 (-23 %) | igual |
| criatura | 43,2 | | 22,4 | usado 64,59 -> 64,8; > 1,5x 7,41 -> 7,43; racord 23,1 -> 23,9; 68 islas |
| midnight | 37,4 | | 21,7 | igual |

La línea base se midió con el equipo más cargado que ahora; la columna `main` es la comparación justa. Lo que queda es casi todo Unfold3D
(humano: 7,4 s de los 9 s; prop: 6,2 s) más 1,3 s de rayos de visibilidad en el prop.

**Probado y descartado:**

- Visibilidad por rayos calculada solo en los vértices que hacen falta: en el prop se acaba usando casi toda la malla (159.454 rayos
  igual que antes), 0 s de ganancia.
- Volver a desplegar solo las islas que cambian en el segundo intento de las costuras de sastre (en vez de rehacer la copia): el humano pasaba
  de 28 a 30 islas, > 1,5x de 3,51 a 3,81 % y el tiempo casi no bajaba (9,2 s frente a 8,9 s).
- Candidatos del empaquetado final (`best_layout`, `uv_pack_tries` = 3), textura aprovechada de cada uno y lo que tarda:

| modelo | base (512, 5 pasadas) | 1024, 5 pasadas | 512, 10 pasadas |
|---|---|---|---|
| humano | 60,258 (0,24 s) | 60,258 (0,69 s) | 60,258 (0,45 s) |
| prop | 59,135 (0,20 s) | 59,137 (0,56 s) | 60,288 (0,37 s) |
| hardsurface | 56,010 (0,19 s) | 56,030 (0,50 s) | 56,010 (0,36 s) |

  El candidato de 1024 es el más caro y en estos tres modelos no aporta nada (+0,002 y +0,02 puntos); el de 10 pasadas solo gana en el
  prop (+1,15). No se ha tocado (afectaría a otras mallas, como el águila, que no están en el banco); quitarlo ahorraría 0,5-0,7 s.

## Medidas de UV en el núcleo y una nota única de UVs (3 de noviembre)

**Qué había.** Las UVs solo se medían dentro de Maya (`uv.py`: `stretch`, `uv_area`, `check`) y en el banco (`seam_continuity`, dentro del trabajador
de `tests/bench.py`). `quality.py` mide el retopo, no las UVs. Para juzgar UVs fuera de Maya, o reutilizar las medidas, hacían falta sobre arrays.

**Qué hay.** `mbl_engine/uv_quality.py` (solo numpy, nada de Maya). Entrada: `points` (N x 3), `faces` (índices de punto por cara), `uv_points` (M x 2) y
`uv_faces` (por cara, los índices de UV de sus esquinas en el mismo orden; vacía si la cara no tiene UVs). Es lo que dan `getUVs` y `getAssignedUVs`, o los
`vt` de un OBJ. `report()` devuelve:

| cifra | qué es | de dónde sale la definición |
|---|---|---|
| `overlapping_faces` | caras cuyo interior en UV entra más de 1e-6 en el de otra (tocarse por arista o esquina no cuenta) | `polyUVOverlap` de Maya (`uv.check`) |
| `flipped_faces` | caras con área UV con signo negativo (en espejo) | `uv.flipped_faces` |
| `stretch_mean`, `over_1_5_percent` | distorsión de área de cada cara frente a 3D, relativa a la densidad media; media ponderada por área 3D (tope 4x por cara) y % de caras con más de 1,5x | `uv.stretch`, igual |
| `used_percent` | suma de las áreas UV frente al cuadrado 0-1 | `uv.uv_area` |
| `islands` | caras unidas por UVs compartidas, como las islas de Maya | `getUvShellsIds` |
| `texel_variation_percent` | desviación típica / media (ponderada por área 3D) de la densidad de texel (raíz de área UV / área 3D) de cada isla | nueva |
| `margin_min` | distancia mínima en UV entre las orillas de dos islas distintas (`None` con una sola isla; se busca hasta 0,02 y más lejos da 0,02) | nueva |
| `seam_percent`, `racord_rot_lt_15`, `racord_scale_lt_10`, `racord_percent` | longitud de cortes y % de ella donde las dos orillas casan: giro < 15 grados, escala dentro del 10 %, y las dos cosas a la vez | `seam_continuity` del banco, igual |

`grade(rep)` da `(nota 0/1/2, razones)` con las constantes `LIMITS`, propias del módulo (no son las de `quality.py`, que no se tocó).

**Cómo se calcula lo que no es una suma.** Los solapes: cada cara se parte en triángulos (los quads por la diagonal que queda dentro, por si son cóncavos), una
rejilla uniforme reparte las cajas de los triángulos y solo se comparan los que comparten celda (cada par sale una vez, en la celda de su esquina común más
baja); entre ellos, ejes separadores con tolerancia. 40.000 caras en un plano: 0,3 s. El margen: las aristas UV de una sola cara son las orillas; la misma
rejilla da los pares de orillas de islas distintas a menos de 0,02 y se mide la distancia entre segmentos. Las islas: componentes conexas por salto de
punteros con numpy, como en `uv_seams.py`.

**Validación contra Maya** (`test_uv_quality_matches_maya`, mallas de 6 a 140 caras desplegadas con `uv.unwrap`, y tres modelos del banco con el retopo de Maya a 5.000 caras
y el paso de UVs completo, medidos con un script aparte bajo el bloqueo del banco):

| malla | estiramiento medio (núcleo / Maya) | > 1,5x | aprovechado | islas |
|---|---|---|---|---|
| esfera 140 caras | 1,16594 / 1,16594 | 0 / 0 | 43,4081 / 43,4081 | 3 / 3 |
| toro 112 caras | 1,01623 / 1,01623 | 0 / 0 | 54,1503 / 54,1503 | 5 / 5 |
| cubo 6 caras | 1,03798 / 1,03798 | 0 / 0 | 54,5093 / 54,5093 | 3 / 3 |
| prop 5.049 caras | 1,0687 / 1,0687 | 1,228 / 1,228 | 60,2777 / 60,278 | 21 / 21 |
| humano 5.095 caras | 1,0772 / 1,0772 | 3,513 / 3,513 | 60,3128 / 60,313 | 28 / 28 |
| hardsurface 4.738 caras | 1,1413 / 1,1413 | 9,730 / 9,730 | 56,0299 / 56,030 | 6 / 6 |

0 solapes y 0 espejos cuando `uv.check` da 0. Con una isla de la esfera puesta encima de otra, `polyUVOverlap` cuenta 129 caras y el núcleo 129; con otra
isla en espejo, las caras en espejo coinciden. La continuidad en los cortes da lo mismo que el banco: giro < 15 grados 15,73 / 27,46 / 2,91 %
(prop, humano, hardsurface) y costuras 5,95 / 7,28 / 3,84 %, igual que `baseline.json`. La medida entera tarda 0,09 a 0,14 s con 5.000 caras.

**Umbrales (`LIMITS`, provisionales).** Se eligieron para que lo que ya hace bien el plugin quede en 0 o 1, y nada de la línea base en 2 salvo que tenga
solapes o espejo (no tiene ninguno). Con las cifras de `tests/baseline.json` (2 de noviembre, 10 resultados) y las del núcleo sobre el prop, la esfera, el toro y el cubo:

| cifra | línea base / medido | revisar | malo |
|---|---|---|---|
| solapadas, espejo | 0 | cualquiera | 0,5 % de las caras |
| caras sin UVs | 0 | cualquiera | 1 % |
| estiramiento medio | 1,07 a 1,19 | >= 1,30 | >= 1,60 |
| caras > 1,5x | 1,2 a 9,7 % | >= 12 % | >= 30 % |
| variación de texel entre islas | 0 a 0,14 % (núcleo, 3 modelos) | >= 25 % | >= 60 % |
| aprovechado | 49 a 65 % | <= 40 % | <= 20 % |
| continuidad (giro y escala) | 0,24 a 17 % (núcleo; giro solo: 2,9 a 27,8 % en la línea base) | <= 0,1 % | no hay |
| margen entre islas (UV) | 0,0039 a 0,0061 | <= 0,001 | no hay |

Con ellos, el prop, el humano y el hardsurface salen con nota 0 (y la esfera, el toro y el cubo de las pruebas, con 0 o 1: el cubo y la esfera tienen 0 % de
continuidad).

Las que no se medían en el banco (solapes, espejos, densidad de texel entre islas, margen) se calibraron con lo que da el plugin sobre las mallas de arriba y el
sentido común: Maya reparte la densidad igual en todas las islas (variación 0 a 0,14 %), deja de 0,0039 a 0,0060 de margen (relleno de 16 px a 4096 = 0,0039),
y no deja solapes ni espejos (`assert_good_uvs`). Por eso un solo solape o espejo ya avisa (nota 1) y desde el 0,5 % de las caras es malo (nota 2).
La continuidad en los cortes no es un defecto en piezas duras (giros de 90 grados entre caras de una caja: el hardsurface de la línea base tiene un 2,9 % de
giro y solo un 0,24 % con giro y escala a la vez), así que solo avisa si casi nada continúa (<= 0,1 %) y nunca da nota 2 por sí sola; el margen, igual
(un margen de cero tampoco rompe nada, solo mancha el borde con mipmaps).
`test_uv_quality_is_fast_and_grades_the_baseline_as_acceptable` repite las cifras de la línea base contra `grade()` para que un cambio de umbral que los
mande a 2 salte.

**Lo que no se ha hecho / dudoso.**

- No se ha conectado al semáforo ni al banco (lo decide el probador; el semáforo lo conectó después la tarea U2, ver «El semáforo de UVs pasa por el juez de UVs»). Los nombres de las cifras (`used_percent`, `over_1_5_percent`, `racord_*`, `islands`,
  `stretch_mean`) son los de `uv` en `baseline.json`, para poder compararlas directamente.
- `used_percent` suma áreas, como `uv.uv_area`: con solapes pasa de lo que cubre de verdad la textura, y con UDIM pasa de 100. Calcular la unión (rasterizando)
  no encaja con la validación de ±0,5 puntos frente a `uv.uv_area`.
- Los solapes cuentan caras con interior compartido; si `polyUVOverlap` cuenta también caras que solo se tocan, en casos raros (islas pegadas sin margen) Maya
  daría más. En las pruebas coinciden (129 y 129).
- La continuidad en los cortes compara UVs por índice, como el banco: dos UV con las mismas coordenadas pero distinto índice cuentan como corte.
- El margen se mide solo hasta 0,02: más allá da 0,02.
- Los umbrales de densidad de texel entre islas (25 %, 60 %), margen y espejos no tienen ninguna malla mala medida con la que contrastarlos: solo se sabe
  lo que da el plugin cuando va bien.
- Con la línea base solo se pudo medir de nuevo el prop, el humano y el hardsurface (retopo de Maya); de los otros siete resultados (criatura, midnight y el Motor MBL)
  solo hay las cifras que guarda `baseline.json`, que se pasan por `grade()` en la prueba rápida (sin margen ni texel, puestos como los del plugin).
- Un único enfoque (cajas por rejilla + ejes separadores para solapes, segmentos para el margen): no hizo falta probar otro, no se llegó al criterio de abandono.

## El semáforo de UVs pasa por el juez de UVs (3 de noviembre, tarea U2)

**Qué cambia.** `status.uv_status` calcula también `uv_quality.report` y `uv_quality.grade` con las UVs de la malla, y el nivel del paso UVs es el peor entre lo que ya
calculaba y la nota del juez (0 verde, 1 amarillo, 2 rojo). Los umbrales son los de `uv_quality.LIMITS`, sin tocarlos. El semáforo nunca sale más verde que `grade()`.

**Cómo.**

- `status.uv_arrays(transform)` saca los arrays que pide `report()`: puntos y caras en mundo (`retopo.mesh_arrays`) y `getUVs` / `getAssignedUVs` de `MFnMesh`
  (el mismo formato que usa `test_uv_quality_matches_maya`). `status.uv_judge_report` llama a `report()`.
- Cada cifra de `LIMITS` sale como un aviso con su nivel y su «→ qué tocar», con nombres que existen en la ventana (la mayoría están ahora dentro de *Avanzado*):

  | cifra del juez | aviso | qué tocar |
  |---|---|---|
  | solapadas / en espejo / sin UVs | n.º de caras y % | repetir *UVs*; Costuras *Máxima resolución* o subir *Dividir islas* |
  | estiramiento medio | `Estiramiento medio de 1,xx x` | subir *Optimize* o *Dividir islas*, o Costuras *Máxima resolución* |
  | densidad de texel entre islas | `% de variación` | repetir *UVs* (el plugin da la misma densidad a todas; escalar islas a mano la rompe) |
  | textura aprovechada | `Solo el x % ...` | subir *Intentos de empaquetado* o *Calidad de layout*, bajar *Padding*, marcar *UDIM* |
  | cortes que no casan | `solo el x % de su longitud continúa` | bajar *Dividir islas* o elegir *Costuras escondidas*; dejar marcado *Islas rectas y de pie* y desmarcado *Permitir rotar islas* |
  | margen entre islas | margen mínimo en px a la resolución elegida | subir *Padding* y repetir *UVs* |

- Solo se añade un aviso del juez si es **más duro** que lo que `uv_status` ya avisaba con sus comprobaciones (así no sale dos veces). Solapes, espejos y caras sin UVs de
  Maya ya son rojo con una sola cara, así que el juez solo aporta si Maya (`polyUVOverlap`) y el núcleo no coinciden. El estiramiento de antes (10 % / 20 % de caras de más
  de 1,5x) ya es más duro que el del juez (12 % / 30 %). La textura aprovechada de antes (50 % / 35 %) también, salvo con *Costuras escondidas* (35 % / 25 %): ahí un 35-40 %
  lo deja pasar el semáforo de antes y no el juez, y sale amarillo. Son nuevos el estiramiento medio, la densidad de texel entre islas, el margen y la continuidad de los cortes.
- Como en el retopo, si `grade()` diera más nota que todos los avisos juntos se añade un aviso con sus razones (red de seguridad), y si el cálculo falla el paso sale en
  amarillo con un aviso (traza en el Script Editor) y las comprobaciones de antes siguen.
- Con UDIM, `used_percent` suma áreas (cada tile es un cuadrado 0-1) y pasaría de 100: se reparte entre los tiles que ocupan las islas.
- Coste: 0,19 s el juez y 0,23 s `uv_status` entero con una malla de 4.704 caras (esfera de cubo desplegada con `uv.unwrap`); 0,05 s el juez con 4.900 caras en un plano. Con 152.100
  caras (el máximo para UVs es 150.000) el juez tarda 2,1 s; el `uv_status` entero 38 s, casi todo en las comprobaciones de antes (`uv.check` con `polyUVOverlap`), no en el juez.

**Pruebas nuevas** (`test_status_uv_judge`, sobre mallas con las UVs exactas que se les da, sin depender de Unfold3D): dos islas con la misma densidad, separadas y llenando la
textura, una sola isla que llena el cuadro y dos islas en dos tiles UDIM salen en verde; una isla sobre otra, una en espejo (también con el informe de Maya diciendo que no hay
nada, para que solo pueda verlo el juez), una con mitad de densidad, dos separadas por 0,0005 en UV, un 38 % de textura con *Costuras escondidas* y un corte con la segunda isla girada
90 grados salen en no verde con su consejo; si el juez lanza una excepción sale amarillo. En todos, el nivel es >= `uv_quality.grade()`.

**Banco** (`python tests\bench.py --models <bench_models.json> --only human,prop,hardsurface,criatura,midnight --engines maya --out %TEMP%\u2_1.json --compare tests\baseline.json`,
5.000 caras, motor Maya, unos 2,5 min): el paso UVs de los cinco sigue en verde (0), como en la línea base; ninguno tiene nota 1 o 2 del juez (el prop, el humano y el hardsurface ya se
sabía que daban 0; la criatura y Midnight, que solo se habían pasado por `grade()` con las cifras guardadas, también). Es decir: ninguno de los verdes de hoy pasa a amarillo.

**Lo que no se ha hecho / dudoso.**

- La densidad de texel entre islas y el estiramiento van muy ligados: con dos islas de la misma área 3D, llegar al 25 % de variación (amarillo) obliga a que la mitad de las caras pasen del
  1,5x de estiramiento (rojo por el aviso de antes). El aviso de densidad solo sale sin el de estiramiento cuando las islas tienen áreas muy distintas; en la prueba salen los dos.
- Los umbrales de `LIMITS` son los del probador y no se han tocado. `racord_percent` <= 0,1 % da amarillo: una esfera desplegada en tres islas puede tener 0 % de continuidad
  y no hay forma de dejarlo verde salvo dar otro corte; la esfera de `test_status` (3 islas) sale en verde; en la esfera de cubo de 4.704 caras de las medidas la continuidad (17 %) no da aviso (la dejó en rojo la textura aprovechada, un 30 % con los ajustes rápidos de esa prueba, no el juez).
- En UDIM no se mide la textura aprovechada de antes (no tiene sentido con varios tiles): solo la del juez, repartida entre los tiles ocupados.

## El motor por defecto pasa a Maya y la ventana se ordena (3 de noviembre, tarea U2)

**Decisión del proyecto.** El Motor MBL no pasa a ser el motor único: queda visible solo como experimental y no se quita ningún otro motor. En el banco (`tests/baseline.json`,
5 modelos a 5.000 caras, fealdad = `defect_score` del juez de retopo) Maya rinde mejor que el Motor MBL en cuatro de los cinco:

| modelo | fealdad Maya | fealdad Motor MBL |
|---|---|---|
| humano | 1,70 | 5,76 |
| prop | 0,92 | 5,66 |
| criatura | 3,90 | 4,68 |
| midnight | 3,15 | 5,79 |
| hardsurface | 0,91 | 0,00 |

Con quads deformados del 1,5 % (Maya) frente al 12,0 % (Motor MBL) en el humano, y 0,3 % frente a 2,5 % en el prop. El hardsurface es la excepción (el Motor MBL sale con nota 0 y Maya con 1).

**Motor por defecto.** `config.DEFAULTS["retopo_engine"]` pasa de `"auto"` a `"maya"`. `SETTINGS_VERSION` sube a 8 y `REPLACED_VALUES[8] = {"retopo_engine": {"auto": "maya"}}`: los JSON
guardados con una versión anterior que tenían Automático pasan a Maya una vez; quien eligió Maya, Instant Meshes o el Motor MBL conserva el suyo; con la versión 8 Automático se respeta. Los
valores de `REPLACED_VALUES` se aplican en orden, así que QuadWild (versión 6 o menos) pasa por Automático y acaba en Maya. **No se puede distinguir** a quien eligió Automático a mano
antes de la 8 de quien lo tenía por ser el defecto (el JSON guarda todas las claves): los dos pasan a Maya y pueden volver a elegirlo.

**Ventana.**

- A la vista, en *Ajustes principales*: **Motor** (con su nota), **Caras objetivo**, **Recomendar caras** y **Resolución textura**. Debajo, siempre visibles, los botones (*Todo* arriba, y *Limpiar*,
  *Retopo*, *UVs*), la barra de progreso, el semáforo y el log.
- Plegado en un único grupo **Avanzado** (casilla en el título, cerrado por defecto) y con subgrupos: *Limpieza*; *Retopo* (simetría, opciones del Motor MBL y de Instant Meshes, ángulo de aristas duras,
  reproyección, polos, relajado, piezas pequeñas, agujeros, tiempo máximo, *Diagnóstico*); *Instant Meshes* (ruta del programa, solo con ese motor); *Guías de flujo*; *Pincel de detalle*; *UVs* (todas menos la resolución).
  Los 45 widgets de `self.widgets` siguen con su nombre (`collect_settings` y las pruebas dependen de ellos); no se quitó ninguna opción.
- El desplegable: Maya (polyRetopo, recomendado), Automático, Motor MBL (experimental), Instant Meshes. La nota del Motor MBL empieza por «EXPERIMENTAL, no recomendado para trabajar» y la de Maya dice que es el recomendado.
  Se quitó del Motor MBL el texto de «la mejor fidelidad de forma» (la línea base ya no lo respalda: error medio de forma 0,155 % frente a 0,084 % en el humano).
- Con Instant Meshes elegido y su programa sin encontrar, *Avanzado* se abre solo (la ruta está dentro).
- Los consejos del semáforo que decían «(en 'Más opciones')» dicen ahora «(en 'Avanzado')» (y ya no nombran a QuadWild, que se quitó).

**Pruebas.** `test_ui_builds` cambia lo que comprueba el orden del desplegable (`["maya", "auto", "mbl", "instant_meshes"]`) y el motor al que cae un valor desconocido (`"maya"`, el nuevo defecto) y
añade: la nota experimental, que ningún widget se quitó, que solo motor / caras / resolución quedan fuera de *Avanzado* y que *Avanzado* se abre con Instant Meshes sin programa.
`test_config_removed_engine` espera `"maya"` en vez de `"auto"` (QuadWild pasa por Automático hasta Maya; Automático guardado con la versión actual se respeta) y la nueva
`test_config_default_engine_migration` prueba la migración (versiones sin número, 3, 5, 6 y 7 con `auto` a `maya`; `maya`, `mbl` e `instant_meshes` se conservan; `auto` con la versión 8 se conserva; guardar y volver a cargar no repite la migración).

**Banco con el motor por defecto** (el mismo comando de arriba; `bench.py` copia `config.DEFAULTS` y pone el motor de `--engines`, así que con el defecto en Maya y con `--engines maya` es lo mismo).
Humano: 5.095 caras, 99,8 % quads, 174 polos, error de forma 0,084 %, fealdad 1,701, UV 60,3 % usado, igual que la línea base. El prop (5.049 caras, 53 polos, 0,087 %, 0,915), el hardsurface (4.738, 34, 0,019 %, 0,908),
la criatura (5.721, 363, 0,129 %, 3,901) y Midnight (5.355, 258, 0,138 %, 3,148) también. Las únicas diferencias con `baseline.json` son el paso Retopo del semáforo (hardsurface 0 a 1, humano 0 a 2, prop 1 a 2: viene del
juez de retopo, anterior a esta tarea) y dos centésimas de `over_1_5_percent` (Midnight 6,52 a 6,54, prop 1,23 a 1,25; `uv.py` no se tocó).


## Margen según la resolución y bordes de material como costuras (3 de noviembre)

**Margen entre islas (B5).** Antes eran 16 px fijos: demasiado para 2K y poco para 8K cuando se baja a mipmaps. Ahora `uv_padding_px = 0` (el valor por defecto) es automático: `config.padding_px(settings)` da 1/256 del lado de la textura, es decir 8 px a 2048, 16 a 4096 y 32 a 8192 (otras resoluciones, la misma proporción, mínimo 2 px). Un número mayor que 0 se respeta tal cual. Lo usan el layout 0-1, el reparto en UDIMs y el `roomspace` de Unfold3D (`uv.py`); la etiqueta de la interfaz es «Padding (0 = automático)». Versión 8 de los ajustes: `REPLACED_VALUES[8] = {"uv_padding_px": {16: 0}}`, así quien tenía el 16 antiguo pasa a automático (a 4096 es lo mismo) y quien eligió otro número lo conserva. Quien eligió un 16 a mano antes de la versión 8 no se distingue del valor por defecto antiguo.

Medido con `test_uv_padding_follows_resolution` (esfera, cubo y cilindro sueltos, 7 islas, `uv_quality.island_margin`):

| textura | padding | margen mínimo medido | a 1/8 de la resolución (mipmap) |
|---|---|---|---|
| 2048 | 8 px (auto) | 11,49 px | 1,44 px |
| 4096 | 16 px (auto) | 22,98 px | 2,87 px |
| 8192 | 32 px (auto) | 45,96 px | 5,75 px |
| 2048 | 24 px (manual) | 32,56 px | 4,07 px |

`u3dLayout` deja algo más de lo pedido (en esta malla, 1,44 veces el `shellSpacing`), así que a 1/8 de la resolución sigue habiendo más de 1 px entre islas. La prueba exige margen ≥ padding − 1 px y ≥ 1 px a 1/8. Con el padding automático a 4096 (= 16 px) las UVs salen iguales que antes: el banco (`--only human,prop,hardsurface --engines maya`) da las mismas cifras de UV que `baseline.json` (aprovechado 60,3 / 60,3 / 56,0 %, caras > 1,5x 3,5 / 1,2 / 9,7 %, racord 27,46 / 15,73 / 2,91 %).

**Bordes de material como costuras (B8).** Si la malla tiene 2 o más materiales (shadingEngines por caras, que es lo que deja el retopo de A2 en el `_retopo`), las aristas entre caras de materiales distintos son costura: cada material tiene sus propias islas y se puede pintar y exportar por separado. `uv.material_edges` las saca de `getConnectedShaders`; con un solo material devuelve una lista vacía y el código sigue exactamente el camino de antes (la prueba compara las UVs con y sin la opción `uv_material_seams`, y el banco da lo mismo que la línea base). Va en el paso de costuras de `_unwrap`: entran en la semilla de `seed_uvs` junto a las aristas por ángulo y se cortan explícitamente con `polyMapCut` (la proyección de partida puede dejar un borde de material dentro de un mismo plano y la semilla solo cose, no corta). Unfold3D añade después sus cortes de despliegue y `fix_charts` completa los que falten para que cada isla se pueda aplanar (el cinturón de una esfera de tres bandas es un tubo y necesita su tajo).

Lo que hizo falta en `uv_seams.py` es el concepto de **costura fija** (`SeamGraph.fixed`, un `frozenset` de aristas; vacío por defecto, así no cambia nada sin materiales): `chains()` no mezcla tramos fijos y libres (los separa donde uno sigue en el otro); `reroute_slits`, la recolocación de tramos, la eliminación de tramos visibles y la unión de islas diminutas se saltan los tramos fijos (las islas de otro material no se cosen); `symmetrize_seams` y `optimize_seams` los vuelven a añadir al resultado. Sin esto, el optimizador de costuras quitaba bordes: con dos mallas de prueba (toro con dos materiales partido por un plano, esfera con un material por octante) la longitud de borde que seguía siendo costura bajaba al 93,7 % y al 73,1 % con el estilo normal. Con las costuras fijas es el 100 % en las dos, y en la esfera de tres bandas (con el estilo normal y con el escondido) también.

| prueba | aristas de borde | longitud que es costura | islas | solapes / espejos |
|---|---|---|---|---|
| esfera 3 bandas, costuras normales | 48 | 100 % | 8 | 0 / 0 |
| esfera 3 bandas, costuras escondidas | 48 | 100 % | 5 | 0 / 0 |
| esfera 8 octantes, costuras normales | 96 | 100 % | 24 | 0 / 0 |

Ninguna isla mezcla dos materiales, y con `uv_material_seams = False` los bordes dejan de ser costura (esfera de 3 bandas y de octantes: menos del 95 %).

**Lo que no hace.** Con `uv_respect_existing` y una malla que ya trae costuras se respetan las que tiene y no se añaden las de material (son las del artista). Con las costuras simétricas y materiales repartidos sin simetría, el reflejo de un borde de material también se corta aunque en ese lado no haya cambio de material (sale una costura de más, nunca una de menos). Las caras sin material asignado cuentan como un material más. Si el reparto de materiales es muy fragmentado (por ejemplo un material distinto por cara) habrá una isla por trozo; la opción `uv_material_seams` (sin casilla en la interfaz) lo apaga.

## Piezas repetidas, una sola vez, y densidad de texel común (3 de noviembre, tarea U4)

**Piezas repetidas (B4).** Veinte tornillos copiados no necesitan veinte retopos. `mbl_engine/duplicates.py` (núcleo, sin Maya) agrupa las mallas que son la misma pieza: la misma topología vértice a vértice (nº de vértices y huella de los índices de las caras) y los puntos del mundo de una llevados a los de la otra por un giro y un desplazamiento (ajuste de Kabsch por mínimos cuadrados y comprobación punto a punto; error máximo `1e-4` de la diagonal más una holgura de float32 respecto a la mayor coordenada). Decisiones:

- **Escala y espejo no se agrupan**, aunque la escala sea uniforme: una copia al doble de tamaño tiene otra densidad de texel y otras caras por área, y un espejo invierte el sentido de las caras. Solo cuentan como iguales las del mismo tamaño. Se compara en el mundo, así que da igual si la copia está congelada, tiene la transformación en el objeto o está en otro grupo.
- La topología se compara tal cual (mismo orden de vértices y caras: es lo que da duplicar o copiar un objeto). Una malla rehecha con otro orden de vértices no se reconoce y se procesa entera: no falla, solo no se aprovecha.
- Una copia con un vértice desplazado un 1 % de su tamaño ya es otra pieza (prueba con una esfera: 7,5 %; núcleo: 1 %); el ruido de float (1e-7) no separa copias.

`pipeline.run` (con `pipeline_reuse_duplicates`, activo por defecto, y solo si el **retopo** está entre los pasos: es la malla nueva lo que se copia) procesa entera la primera malla de cada grupo y a las demás les copia el resultado con `cmds.duplicate`: mismas caras y mismas UVs, sobre la transformación, el grupo, los pivotes y los materiales de **su** original (cada copia reparte los materiales por zonas desde su propio original: una con otro material sale con el suyo), con el original oculto y enlazado (`mblSource`). El semáforo y los informes son los de la pieza hecha. Si copiar falla, esa malla se procesa entera. Dos caminos de colocación:

- **Duplicado de verdad** (la misma malla en el espacio de objeto, otra transformación): la matriz de objeto a objeto, `M = W_hecha · T · W_copia⁻¹`, sale la identidad y solo se mueve el resultado al grupo de la copia y se le ponen sus atributos. Cuesta ~3 ms por copia.
- **Copia congelada, girada o con otros puntos en el objeto**: se pasa por el mundo (aplanar, mover con la matriz rígida, aplanar, y `_match_placement`, el mismo de A2). Congelar una escala negativa da la vuelta a las caras (comprobado: orientación hacia fuera pasa de 0 a 1 al congelar una esfera con escala −1), así que se devuelven antes de seguir.

Medido con `mayapy` (toro de 24x12, 288 caras, motor Maya, 600 caras objetivo, limpieza + retopo + UVs, tras un calentamiento): 1 pieza 1,6–2,2 s; 20 copias 1,68–2,7 s según el momento (el PC no estaba en silencio: el tiempo de la pieza hecha varía ±0,5 s de una pasada a otra, y las medidas más tranquilas dieron 1,02x y 1,09x). Lo que cuesta de más la reutilización, medido por separado, es **agrupar 20 mallas 15 ms** y **copiar cada una ~3 ms (58 ms las 19)**, es decir, un 3–4 % de una pieza; sin reutilizar, 1,8–2,4 s por pieza. Antes de añadir el camino rápido de duplicados (todo por el mundo) cada copia costaba ~8 ms. La prueba `test_duplicates_twenty_copies_cost_about_one` anota los dos tiempos y exige que el retopo se haga una sola vez y que el total sea menos del doble de una pieza. Con una sola malla o con mallas distintas el camino es el de antes.

Pruebas: núcleo en `test_engine.py` (`test_duplicates_*`: agrupar copias con giro/movimiento lejos del origen y en float32, separar vértice movido/escala/espejo/otra topología, planos, velocidad de 20 copias de 5.000 vértices) y Maya en `run_tests.py` (`test_duplicates_are_retopologized_once_and_copied`: cinco copias con dos materiales, otro grupo, otro material y una congelada, un solo retopo; `test_duplicates_deformed_copy_is_its_own_group`; `test_duplicates_mirrored_copies_keep_their_normals`; `test_duplicates_undo_and_redo_in_one_step`; `test_duplicates_twenty_copies_cost_about_one`).

**Densidad de texel común (B6).** Con `uv_common_texel` (apagado por defecto: con varias mallas cambia el resultado de cada una, que deja de llenar su 0-1), después de desplegar todas las mallas se mide `uv.texel_density` de cada una y la **común es la menor**, la de la malla más grande: es la más alta que le cabe en su textura (otra opción, la media ponderada, haría que la más grande no cupiera). Las demás bajan a ella con `uv.scale_to_density`: se escalan las islas (`polyEditUV`) y se vuelven a colocar sin cambiar su tamaño (`u3dLayout` con `preScaleMode=0` y `layoutScaleMode=1`, con el padding y los giros de siempre: lo comprobado es que la densidad no cambia y no hay solapes), o con `_layout_udim` a esa densidad en modo UDIM. Una malla a menos de un 2 % de la común no se toca; **la de referencia queda idéntica** a como saldría sin la opción, y con **una sola malla no cambia nada** (comparadas las UVs). Va en una segunda pasada porque la densidad natural de cada malla depende de cuánto llena su textura, que solo se sabe tras el empaquetado. Las copias de una pieza repetida (B4) reciben las UVs de su pieza con `polyTransfer` en vez de repetir el empaquetado.

Tres toros de 1, 10 y 100 unidades de tamaño, a 4096 px, solo UVs (la prueba `test_common_texel_density_between_meshes` hace lo mismo a 2048):

| | densidad (px/unidad) del pequeño, mediano y grande | máx/mín | textura que usa cada uno |
|---|---|---|---|
| apagado, 0-1 | 1544 · 156 · 16,4 | 94,5 | 55 % · 56 % · 62 % |
| **encendido, 0-1** | 16,35 · 16,35 · 16,35 | **1,000** | 0,01 % · 0,62 % · 62 % |
| apagado, UDIM (1 tile) | 1320 · 135 · 13,4 | 98,2 | 40 % · 42 % · 42 % |
| **encendido, UDIM (1 tile)** | 13,44 · 13,44 · 13,44 | **1,000** | 0,00 % · 0,42 % · 42 % |

Es lo que pide la igualdad: una pieza de 1 unidad junto a una de 100 a la misma nitidez ocupa un 1 % del lado de su textura. El semáforo lo sabe: en una malla bajada a la densidad común no avisa de «poca textura aprovechada» (`report["common_texel"]` en `status.uv_status`, que ignora el aprovechamiento solo en esa malla; el resto de medidas del juez siguen). Con padding fijo en píxeles, una malla muy pequeña tiene islas de pocos píxeles: si el padding no cabe entre ellas, el juez avisa del margen.

**Banco.** `python testsench.py --models ...ench_models.json --only human,prop,hardsurface --engines maya --compare ...aseline.json`: las cifras de retopo y de UV de los tres modelos (caras, quads, polos, error de forma, feo, aprovechado, estiramiento, racord, islas) son idénticas a la línea base; solo cambia `status.retopo` (0 → 1 en hardsurface, 0 → 2 en humano, 1 → 2 en prop), que esta tarea no toca (ni `retopo_status` ni el retopo cambian: no se comprobó si `main` ya sale así contra esa línea base) y que el criterio manda ignorar. Con una sola malla no se busca ninguna repetida ni se iguala nada, así que el camino es el de antes.

**Pendiente / dudoso.** (1) El deshacer de `retopo + uv` deja el retopo de la primera malla en la escena tras un Ctrl+Z (también con una sola malla y sin copias; con solo `retopo` deshace todo): ya pasaba antes de esta tarea, algo del paso de UVs rompe el bloque de deshacer. (2) Las copias no repiten la limpieza en su original (se queda como estaba, oculto): es lo que se quiere, pero el original de una copia no queda «limpio» como el de la pieza hecha. (3) UVs sin retopo (solo `UVs`) no reutilizan nada: las UVs de una malla se escriben en la propia malla y haría falta copiarlas con `polyTransfer`; se puede añadir si se pide. (4) `uv_common_texel` encendido por defecto sería razonable si las mallas comparten textura; se dejó apagado porque cada malla pierde resolución.


## Enderezar tubos: casar las dos orillas de los cortes de una isla (3 de noviembre, tarea U3 = B1)

**Qué.** Después de desplegar y de colocar las costuras, cada isla que tiene un corte contra sí misma (el tajo a lo largo de un brazo, una pierna, un dedo, un cable, una cola) se deforma lo mínimo para que las dos copias de cada arista cortada sean la misma arista en UV: misma dirección y misma longitud. Así la costura es una traslación y una textura con vetas o rayones sigue el mismo rumbo al cruzarla. Núcleo sin Maya en `mbl_engine/uv_straighten.py` (`straighten_island`, `straighten`); `uv.straighten_islands` lo llama entre `_tailor_seams` y `orient_upright`, y solo reescribe las islas aceptadas. Ajuste `uv_straighten_strips` (activado por defecto, casilla «Enderezar tubos» en Avanzado > UVs; ajuste nuevo, no hace falta subir `SETTINGS_VERSION`).

**Cómo.** Por isla: (1) pares de aristas 3D con dos caras en la isla y UV distintas; solo se fuerzan los que giran poco entre sí (límites de 25°, 40°, 60° y 90°) y los que no acaban en un vértice de la isla (la punta de un corte). (2) ARAP local-global (rígido lo más posible) sobre las UV de Unfold3D, con un tirón suave de cada cara hacia el área que le toca por su tamaño 3D (`AREA_PULL` 0,3) y las dos copias de cada arista cortada pesando `mu`; se prueba una escalera de `mu` (30, 10, 3, 1, 0,3) para cada límite de giro. El paso global es denso: se invierte una sola vez el Laplaciano de la isla y cada `mu` se resuelve con Woodbury sobre una matriz k × k (k = pares). (3) Se acepta una solución solo si la isla no estira más que antes (media y caras > 1,5x, con la densidad propia de la isla como referencia, que es lo que deja el empaquetado; tolerancia 0,005 en la media), sin caras en espejo, sin solapes nuevos y casando más longitud de corte; entre las que valen, la que más casa. Islas con n-gons, con más de 1.600 vértices UV o sin cortes propios se dejan (None).

**Por qué no una rejilla recta de filas y columnas.** Es lo que pedía la tarea y se miró primero: en los retopos del banco casi ninguna isla es una rejilla de quads pura (cuadrilátero sin polos dentro): humano 0 de 28 islas; hardsurface 1 (40 caras); midnight 12 (32 caras en total); criatura 9 (22 caras); prop 4 (2.605 caras, pero ya salían rectas y casaban el 98-100 % de su corte propio). Los brazos y las piernas llevan polos, manos y dedos. Enderezar solo rejillas no movía la continuidad. El ARAP con costura de traslación hace lo mismo (filas rectas, orillas paralelas) sin pedir esa estructura.

**Enfoques probados** (continuidad `racord_rot_lt_15`, puntos ganados sobre la línea base; los 4 primeros sobre las UV previas al empaquetado, con el mismo código que se entrega; el giro no cambia al empaquetar porque las islas no giran):

| enfoque | humano | prop | midnight | criatura |
|---|---|---|---|---|
| 1. rejillas puras (la tarea tal cual) | 0 | 0 | 0 | 0 |
| 2. ARAP con costura de traslación, sin tirón de áreas, estiramiento sin subir | +2,3 | +3,9 | +5,2 | +2,2 |
| 3. + tirón hacia el área ideal (0,5) y escalera de `mu` | +12,6 | +6,3 | +9,2 | +2,6 |
| 4. + límites de giro escalonados (25-40-60-90) y tirón 0,3 (el entregado) | +10,6 | +6,7 | +11,1 | +5,6 |
| referencia: pares de menos de 45° con `mu` ≥ 10, sin tirón de áreas y sin aceptar por estiramiento | +14,0 | +6,1 | +7,5 | +6,3 |

Sin el tirón hacia el área (2) casi nada pasa la regla de no estirar, porque Unfold3D ya deja las islas cerca de su mínimo de distorsión y cualquier deformación sube la media; con él las islas gastan un poco del margen que dejó Unfold3D (la media baja, en vez de subir) y entran las deformaciones grandes. Otra prueba descartada: tolerancia de estiramiento 0,02 en vez del tirón (humano +11,6 y midnight +8,1, pero la media de estiramiento sube).

**Cifras del banco** (`python tests\bench.py --models <bench_models.json> --engines maya --out ... --compare tests\baseline.json`, commit `e4de94b`; los dos motores no se midieron, solo `maya`):

| modelo | continuidad (giro < 15°) | textura aprovechada | caras > 1,5x | estiramiento medio | escala dentro del 10 % | islas |
|---|---|---|---|---|---|---|
| humano | 27,46 → 38,11 (+10,7) | 60,31 → 60,97 | 3,51 → 3,06 | 1,077 → 1,072 | 56,8 → 60,7 | 28 → 28 |
| prop | 15,73 → 22,72 (+7,0) | 60,28 → 61,36 | 1,23 → 0,79 | 1,069 → 1,065 | 63,3 → 61,7 | 21 → 21 |
| hardsurface | 2,91 → 2,91 | 56,03 → 56,03 | 9,73 → 9,73 | 1,141 → 1,141 | 54,1 → 54,1 | 6 → 6 |
| criatura | 23,11 → 29,74 (+6,6) | 64,59 → 66,20 | 7,41 → 6,45 | 1,120 → 1,113 | 48,3 → 53,0 | 68 → 68 |
| midnight | 15,53 → 27,25 (+11,7) | 56,07 → 57,35 | 6,52 → 5,60 | 1,140 → 1,123 | 47,6 → 52,6 | 43 → 43 |

Ninguna de las cinco mallas queda con solapes ni caras en espejo (medido con `uv_quality.report` sobre los mismos retopos). El resto de cifras del banco (retopo, calidad, error) no cambian: solo se toca el paso de UVs.

**Qué no se arregla.**
- **Los cortes entre dos islas distintas.** En el humano, el 41 % de la longitud de corte es de una isla contra sí misma y el 59 % entre islas distintas (855 de 1.459; casa el 37 %): ahí no hay deformación de una sola isla que valga, y girar islas (lo que arreglaría parte) está descartado por «Islas rectas y de pie». Sigue pendiente.
- **Geometría.** Un tubo cónico desplegado es un abanico; ponerlo recto exige cambiar el ancho de las filas, o sea estirar. La regla de no estirar deja pasar lo que cabe en el margen de Unfold3D y rechaza el resto (en el humano se enderezan 16 de 28 islas, 3.864 de 5.095 caras). Los pares que giran más de 90° (pliegues, esquinas de corte en T) no se tocan.
- **Hardsurface:** sin cambios. Sus dos islas grandes tienen 3.157 y 1.021 caras (más de 1.600 vértices UV, tope del sistema denso) y la otra con corte propio es de 40 caras.

**Dos cosas que costaron tiempo.**
- **Hilos de BLAS.** Con los hilos por defecto de OpenBLAS (16) y el equipo ocupado, `np.linalg.inv` de 200 × 200 tardaba 0,16 s y la de 800 × 800 0,5-0,7 s; con un hilo, 1 ms y 0,09 s. `straighten` fija un hilo mientras trabaja y lo restaura (`ctypes` sobre el DLL de numpy; si no lo encuentra, no toca nada). El núcleo tarda 0,75-1,2 s por malla en las cinco del banco (humano ~0,8 s, criatura ~1,2 s), alrededor del 10 % del paso de UVs; los tiempos del banco bailan mucho con la carga (humano 32,8 s en la línea base frente a 10,0 s ahora con el mismo código de UVs).
- **La textura aprovechada es sensible a cualquier cambio.** El empaquetado de Unfold3D mueve ±2 puntos con cambios mínimos en las islas: con el humano y un solo límite de giro (50°), `AREA_PULL` 0,3 / 0,5 / 0,7 / 1,0 dio 61,3 / 57,4 / 57,3 / 59,1 % (línea base 60,31) y continuidad 38,8 / 40,4 / 40,4 / 39,5; con los límites escalonados, 0,2 y 0,3 dieron 59,9 y 61,0 % (38,0 y 38,2). Se eligió 0,3, que además deja los otros tres modelos por encima de la línea base, pero conviene saber que la cifra de un solo modelo no es una garantía: una versión que solo comprobaba los solapes de la solución final (misma idea, 83 llamadas menos a `overlapping_faces`) dio humano 38,44 y 58,70 % (−1,6), y se deshizo por eso.

**Pruebas.** `tests/test_engine.py`: `test_straighten_tube_cut_lengthwise` (tubo en abanico: casa del 0 al 100 %, sin estirar más ni solapes), `test_straighten_ring_of_quads` (anillo de una fila), `test_straighten_leaves_islands_that_are_not_tubes` (rejilla plana, tubo ya casado, n-gon y isla demasiado grande: None), `test_straighten_never_raises_the_stretch` (cinco tubos, entre ellos conos muy cónicos) y `test_straighten_only_touches_the_islands_that_improve`. `tests/run_tests.py`: `test_uv_straighten_tubes` (tubo curvado y cónico en una sola isla: de 17 % a 92 % de corte casado, estiramiento igual, sin solapes ni espejos y sin el aviso cuando la opción está apagada).

**Lo que no se ha comprobado.** Mallas grandes, hasta las 150.000 caras que admite el plugin (el sistema denso crece con el cuadrado de los vértices de la isla; el tope de 1.600 acota la memoria a unas decenas de MB por isla pero no el tiempo de una malla con muchas islas grandes); modo UDIM con 8K; mallas con varios conjuntos de UV; la casilla en la ventana de Maya con la interfaz abierta (solo `test_ui_builds`, que la construye sin pantalla).


## UVs planas: estiramiento por isla y «Ver cuadrícula» (3 de noviembre, tarea U6 = B3): corrección automática NO hecha

**Entregado.** (1) `mbl_engine/uv_metrics.py` (núcleo sin Maya): `face_stretch` y `island_stats` dan el estiramiento por cara y, por isla, estiramiento medio, % de caras > 1,5x y densidad de texel relativa, con las definiciones de `uv_quality.report` (sin tocar sus `LIMITS`); marca como mala una isla con más del 10 % de caras > 1,5x o estiramiento medio > 1,30. (2) Botón «Ver cuadrícula» (Avanzado > UVs, `ui.py` -> `uv.toggle_checker`): material temporal con el checker procedural de Maya en la malla seleccionada; un segundo clic lo quita y devuelve los materiales anteriores (también con materiales por caras); no cambia las UVs. Pruebas: `test_uv_metrics_per_island` (test_engine) y `test_uv_checker_toggle_and_undo` (run_tests, incluye Ctrl+Z).

**Abandonado: la corrección automática de islas malas.** No hay ajuste en `config.py` ni cambio en `_unwrap`: no se implementó, así que el criterio de éxito (caras > 1,5x -30 %) NO se cumple ni se ha medido. Por orden de cerrar rápido (poco uso disponible) la tarea se paró antes del primer enfoque medible; no hay cifras de banco de este trabajo (la línea de partida, tras U3, es humano 3,06 %, prop 0,79 %, hardsurface 9,73 %, criatura 6,45 %, midnight 5,60 %). Ideas sin probar, por orden: (a) `u3dOptimize` con más iteraciones/`power` solo en las islas malas; (b) un corte extra desde la zona de más tensión hasta la costura más cercana y re-desplegar solo esa isla (costaría continuidad y textura aprovechada); (c) relajación local ARAP/área sobre las islas malas reutilizando el sistema de `uv_straighten` (la métrica es solo de área, hay que vigilar que no suba el cizallamiento). Cualquiera debe aceptarse por isla solo sin solapes ni espejos. Para iterar rápido conviene cachear el retopo de cada modelo en un .ma y medir solo `uv.unwrap`.

### Corrección de islas estiradas (U6): medida, no cumple, apagada por defecto

Banco con `maya` (>1,5× / textura aprovechada, antes → con la corrección): humano 3,06 → 3,00 / 60,97 → 58,10; prop 0,79 → 0,38 / 61,36 → 60,90; hardsurface 9,73 → 9,54 / 56,03 → 52,19; criatura 6,45 → 5,75 / 66,20 → 61,99; midnight 5,60 → 5,40 / 57,35 → 58,16. El paso de UVs del humano pasa de ~10 s a 34 s. Criterio (−30 % de caras estiradas, ≤ 2 puntos de textura, ≤ +25 % de tiempo): no se cumple, así que `uv_fix_stretch` queda en `False`. Sigue disponible con la casilla «Corregir islas estiradas». Ideas sin probar en la nota de U6.
