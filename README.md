# MBL Retopo UV

**Prepara cualquier malla 3D para texturizar, animar o usar en VFX, con un clic.** Es un plugin para Maya 2026 que limpia el modelo, le rehace la topología (una malla ordenada de cuadrados, con las caras que pidas) y le crea las UVs, listo para Substance Painter, para el rig y la animación o para simulación y efectos. No hace falta saber modelar ni hacer UVs.

Está pensado para mallas orgánicas que no vienen bien hechas (personajes y criaturas de escaneos, de IA o de sculpts), y también para arreglar los fallos de tus propios modelos: bucles que no casan, deformaciones, subdivisiones que sobran. Para animación y VFX tienes además un [apartado con guías de flujo](#para-animación-y-vfx-guías-de-flujo) para colocar los anillos de topología en codos, rodillas, ojos y boca.

## Qué necesitas

- **Windows 10 u 11** de 64 bits.
- **Maya 2026.**
- **8 GB de RAM** como mínimo. Con modelos de más de 500.000 triángulos, 16 GB.

No hay que instalar nada más: los programas que usa por dentro ya vienen incluidos. Todo se procesa en tu ordenador; el plugin no se conecta a internet ni envía nada.

## Instalar (1 minuto)

1. **Descomprime el ZIP** (clic derecho > *Extraer todo*). No lo abras sin extraer.
2. Abre **Maya 2026**.
3. Arrastra el archivo **`install.py`** de la carpeta descomprimida **al visor de Maya** (la zona donde ves el modelo) y suéltalo.
4. Listo. Se abre la ventana y quedan dos accesos para siempre: el menú **MBL > MBL Retopo UV** y la pestaña **MBL** de la shelf.

Después puedes borrar la carpeta que descargaste: el instalador ya ha copiado el plugin a tus preferencias de Maya (`Documentos/maya/MBL_Retopo_UV`).

**Actualizar:** descarga la versión nueva y repite los pasos 1 a 3. **Quitar:** arrastra `uninstall.py` al visor.

<details>
<summary>Si arrastrar no funciona</summary>

Abre el *Script Editor* de Maya (*Windows > General Editors > Script Editor*), pestaña **Python**, pega esto cambiando la ruta por la de tu `install.py`, y pulsa Ctrl+Intro:

```python
import runpy; runpy.run_path(r"C:\Users\tu_usuario\Downloads\MBL_Retopo_UV\install.py")["onMayaDroppedPythonFile"]()
```
</details>

## Usar

> ¿Primera vez? Hay un [tutorial paso a paso](docs/tutorial.md) con un ejemplo rápido de cada apartado, las guías de flujo y el pincel de detalle.

1. **Guarda tu escena** y selecciona la malla (puedes elegir varias; se procesan una a una).
2. Abre **MBL > MBL Retopo UV**.
3. Pulsa **Todo: limpiar → retopo → UVs**.
4. Cuando acabe, mira el **semáforo** del panel *Estado* (más abajo). Si está en verde, ya está.
5. Exporta la malla nueva (termina en `_retopo`) a Substance Painter. La original sigue en la escena, oculta.

Un solo **Ctrl+Z** deshace todo el proceso. Mientras trabaja, la ventana de Maya puede quedarse quieta unos minutos: es normal. Arriba tienes la barra de progreso y el botón **Cancelar**, y el log escribe qué hace y a qué hora.

También puedes hacer cada paso por separado con los botones **Limpiar**, **Retopo** y **UVs**. Por ejemplo, solo **UVs** si la malla ya tiene buena topología. **Analizar y seleccionar problemas** (en *Avanzado*, apartado *Limpieza*) no cambia nada: solo marca en la escena lo que está mal.

### Lo único que suele hacer falta tocar

Con los valores que trae la ventana, la mayoría de modelos salen bien. Estos son los ajustes que más se notan:

| Ajuste | Para qué sirve | Qué poner |
|---|---|---|
| **Caras objetivo** | Cuántas caras tendrá el resultado (aproximadamente) | 5.000 por defecto. Un personaje medio: de 5.000 a 20.000 |
| **Recomendar caras** (botón bajo *Caras objetivo*) | Mide tu malla y dice cuántas caras hacen falta para no perder puntas ni piezas finas | Púlsalo con la malla seleccionada y pon esa cifra. Si pides menos, el log y el semáforo te avisan. El log avisa también si mucha superficie queda en piezas más finas que 3 aristas (dedos que se tocan, ropa pegada) y propone *Pintar detalle* |
| **Motor** | Quién hace el retopo | **Maya (polyRetopo, recomendado)**, el de por defecto: solo quads, pocos polos y el que mejor rinde en casi todas las pruebas. *Automático* prueba Maya y el Motor MBL y se queda con el mejor; el **Motor MBL** es experimental (ver abajo); *Instant Meshes* es el más rápido y el menos preciso |
| **Calidad** (Motor MBL, en *Avanzado*) | Cuántas pruebas hace antes de entregar | *Normal* por defecto. *Alta* tarda el doble y mejora un poco; *Rápida* para ver cómo queda |
| **Simetría** (en *Avanzado*) | Si el modelo es simétrico, hace una mitad y la refleja | *No* por defecto. *Automática* si tu modelo es simétrico y quieres los dos lados idénticos |
| **Resolución textura** (UVs) | Tamaño de la textura | 4096 por defecto |

La ventana solo enseña lo principal: **Motor**, **Caras objetivo** (con **Recomendar caras**) y **Resolución textura**, más los botones, el semáforo y el log. Todo lo demás está plegado en el grupo **Avanzado** (pulsa su título para abrirlo): *Limpieza*, *Retopo* (simetría, relajado, polos, agujeros...), *Guías de flujo*, *Pincel de detalle* y *UVs*. Está explicado al pasar el ratón por encima de cada casilla. Si no sabes qué hace algo, déjalo como está.

> Si eliges el motor **Instant Meshes**, dentro de *Avanzado* aparece un recuadro con la ruta del programa: no hace falta tocarlo, el programa ya viene incluido y se encuentra solo (si no lo encuentra, *Avanzado* se abre solo para que la indiques).

### El semáforo

Al terminar, el panel **Estado** pone un color a cada paso de cada malla:

- 🟢 **Verde:** listo para Substance.
- 🟡 **Amarillo:** se puede usar, pero hay algo mejorable.
- 🔴 **Rojo:** hay un problema que se notará al texturizar.

En el paso *Retopo*, además de la forma y los polos, el semáforo usa el mismo juez de calidad que las pruebas del motor: avisa de caras dadas la vuelta o dobladas, quads deformados, saltos de tamaño entre caras vecinas y triángulos de más, y nunca sale en verde si el juez lo suspende.

En el paso *UVs* hace lo mismo con el juez de UVs: además de solapes, espejos, estiramiento y textura aprovechada, mira la densidad de texel entre islas, el margen entre ellas y si los cortes casan (que la textura continúe de un lado a otro de una costura), y nunca sale en verde si ese juez las suspende.

Debajo de cada punto amarillo o rojo sale **→ qué tocar**: el ajuste exacto de la ventana que conviene cambiar, o el botón que hay que pulsar. Doble clic en una malla la selecciona. **Comprobar estado** calcula el semáforo de lo seleccionado cuando quieras, sin cambiar nada.

## Lo que conviene saber

- **El Motor MBL es experimental.** Es nuestro motor de retopo (solo quads, sin agujeros), pero en casi todas las pruebas con modelos reales Maya sale mejor en calidad de malla (menos quads deformados y de tamaño más parejo), así que el motor por defecto es **Maya**. El Motor MBL sigue en el desplegable, junto a *Automático* (que prueba los dos y se queda con el mejor) e *Instant Meshes*, para probarlo o compararlo.
- **Los anillos de la malla (los *edge loops*) no siguen la forma como lo haría un modelador a mano**, así que para animar o simular, y sobre todo para deformaciones finas (caras, hombros, codos), revísalos antes del rig y usa las guías de flujo (más abajo).
- **Las piezas más finas que una arista** (plumas, membranas, telas sueltas) pueden salir con pequeños defectos en el borde. El semáforo suele avisar.
- **Para un resultado concreto** (por ejemplo, un anillo alrededor de los ojos) puedes dibujar **guías de flujo** o pintar **detalle** donde quieras más caras. Están en el grupo *Avanzado*.
- **Los cortes de las UVs y los rayones en Substance.** Un corte siempre parte la textura en dos trozos que no se continúan. Si las capas de Substance (grunge, vetas, rayones procedurales) usan **proyección UV**, la unión se nota: ponles proyección **triplanar** o de **espacio mundo**. Lo pintado a mano con el pincel y las máscaras de curvatura u oclusión se pintan sobre el modelo en 3D y casi no lo notan. El plugin deja todas las islas de pie (la textura va en la misma dirección a los dos lados de un corte) y, con *Costuras escondidas*, lleva los cortes a zonas poco visibles.
- **Cuida la memoria de tu ordenador.** QuadriFlow, el motor de fondo del Motor MBL, gasta unos 54 KB por cada cara que se le pide (25.000 caras, 1,5 GB; 60.000, 3,3 GB). Con 16 GB de RAM o menos trabaja una variante a la vez, no usa más de la mitad de la memoria, corre con prioridad baja y, si la memoria libre baja de 1,2 GB, se detiene solo y Maya sigue vivo. Si pides tantas caras que no caben, te dice cuántas puedes pedir.
- La ventana está en español.

## Para animación y VFX: guías de flujo

El plugin no puede decidir dónde van los polos (los vértices que no tienen 4 aristas), y en una malla que se deforma se notan
sobre todo en codos, rodillas, muñecas, hombros, ojos y boca. Lo que sí sigue el retopo son los **anillos que le dibujes**:

1. Selecciona el modelo original, abre el grupo *Avanzado* y busca el bloque **Guías de flujo**. Pulsa **Dibujar guías**.
2. Dibuja un trazo cerrado alrededor de cada zona que se doble: un anillo en cada codo, rodilla y muñeca (cruzando el hueso),
   y otro alrededor de cada ojo y de la boca. Los trazos se pegan a la superficie. Si quieres varios anillos en una articulación,
   dibuja varios trazos paralelos.
3. Pulsa **Terminar guías** (también puedes usar curvas que ya tengas con **Usar curvas seleccionadas**, o quitarlas con
   **Borrar guías**). Los trazos quedan en amarillo, en el grupo `<malla>_mblGuides`.
4. Pulsa **Retopo** o **Todo**. Con guías se usa el motor de Maya, que es el que mejor las sigue (y el de por defecto), y se hace el modelo
   entero (sin reflejar una mitad).
5. Comprueba que los loops de la malla nueva siguen los trazos.

Conviene saber:
- **El Motor MBL no admite guías**: con guías se usa Maya.
- Si con las guías salen más de 1,6 veces las caras pedidas, el plugin repite el retopo sin ellas y lo avisa en el log.
- Las guías ayudan a que haya anillos donde los quieres; **no garantizan** dónde quedan los polos. Revisa la malla antes de hacer el skin.
- Con **Pintar detalle** (mismo apartado) puedes pedir más caras en la cara y las manos.

## Si algo falla

| Problema | Qué hacer |
|---|---|
| Al arrastrar `install.py` sale *«faltan las carpetas…»* | Descomprime el ZIP entero y arrastra el `install.py` de la carpeta descomprimida, no el del interior del ZIP |
| No aparece el menú **MBL** | Reinicia Maya. Si sigue sin salir, abre el Script Editor: ahí aparece el motivo |
| *«No se pudo cargar Unfold3D»* | *Windows > Settings/Preferences > Plug-in Manager*: marca **Unfold3D** como *Loaded* y *Auto load* |
| *«tiene aristas/vértices no-manifold»* al hacer UVs | Pulsa **Limpiar** con *Reparar no-manifold* marcado y repite |
| Maya se cierra de golpe | Casi siempre es falta de memoria. Cierra otros programas pesados y haz el retopo **antes** de las UVs (con **Todo** ya va en ese orden) |
| Tarda demasiado | Baja las **Caras objetivo** o, con el Motor MBL o *Automático*, la **Calidad** (en *Avanzado*) a *Rápida*. El Motor MBL se para solo al llegar a su límite (4,5 minutos, ajustable en *Tiempo máx. Motor MBL*) |
| No me gusta cómo ha quedado | **Ctrl+Z**, y prueba otro **Motor**, otro número de caras o con la **Simetría** apagada |
| Algo raro que no sabes de dónde viene | El Script Editor tiene el detalle completo del error. **Ctrl+Z** deja la escena como estaba |

Tus ajustes se guardan solos en `mbl_retopo_uv.json`, dentro de las preferencias de Maya.

## Para quien quiera mirar por dentro

```
install.py, uninstall.py   instaladores (se arrastran a Maya)
mbl_retopo_uv/             el plugin: ventana, limpieza, retopo, UVs, semáforo
mbl_retopo_uv/bin/         programas de retopo ya compilados (Windows)
mbl_engine/                el Motor MBL: núcleo en Python y numpy, sin nada de Maya
engine/                    código y parches de los programas compilados (cómo recompilarlos)
docs/                      notas técnicas y diseño del Motor MBL
```

- `python install.py --en-sitio` usa la carpeta tal cual, sin copiarla: cómodo para desarrollar.
- Cómo recompilar los programas: [`engine/README.md`](engine/README.md). Después de compilar, `python engine/scrub_paths.py` quita de los `.exe` la ruta de tu carpeta.
- Medidas, decisiones y resultados: [`docs/notas-tecnicas.md`](docs/notas-tecnicas.md).

## Licencia y créditos

El código de este proyecto es de uso libre bajo licencia MIT ([`LICENSE`](LICENSE)). El plugin se apoya en trabajo de otras personas (Instant Meshes, QuadriFlow y más), con sus licencias, en [`CREDITS.md`](CREDITS.md). Las herramientas de Maya que usa (polyRetopo, Unfold3D) son de Autodesk y vienen con Maya.

---

## In short (English)

MBL Retopo UV is a Maya 2026 plugin (Windows) that cleans a mesh, rebuilds its topology as quads and creates its UVs, in one click: ready for texturing (Substance Painter), rigging and animation, or VFX and simulation. The interface is in Spanish.

1. Unzip the download. 2. Open Maya 2026. 3. Drag `install.py` into the Maya viewport. 4. Select your mesh, open **MBL > MBL Retopo UV** and press **Todo** (all steps). Drag `uninstall.py` to remove it. Everything runs locally; nothing is sent over the network.
