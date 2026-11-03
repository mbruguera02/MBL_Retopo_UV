# Créditos y licencias

MBL Retopo UV se apoya en el trabajo de otras personas. Aquí están todos los proyectos que el plugin usa, que hemos modificado o que hemos tomado como referencia, con sus autores y licencias. Cada archivo derivado lleva además el aviso de su licencia original.

## Motores de retopología

| Proyecto | Autores | Licencia | Qué usamos |
|---|---|---|---|
| [Instant Meshes](https://github.com/wjakob/instant-meshes) | Wenzel Jakob, Daniele Panozzo, Marco Tarini y Olga Sorkine-Hornung. *Instant Field-Aligned Meshes*, ACM Transactions on Graphics (SIGGRAPH Asia 2015) | BSD 3 cláusulas | Base del Motor MBL. Nuestra versión añade `--adaptive`, `--density` y `--orient` (ver `engine/README.md` y `engine/mbl_changes.patch`). El ejecutable va en `mbl_retopo_uv/bin`. |
| [QuadriFlow](https://github.com/hjwdzh/QuadriFlow) | Jingwei Huang, Yichao Zhou, Matthias Nießner, Jonathan Richard Shewchuk y Leonidas Guibas. *QuadriFlow: A Scalable and Robust Method for Quadrangulation*, Computer Graphics Forum (SGP 2018) | BSD 3 cláusulas | Motor del Motor MBL (`engine/quadriflow`). Cambios nuestros, recogidos en `engine/quadriflow_mbl_changes.patch`: opción `-density` (tamaño de cara por vértice), arreglo de un operador coma en `subdivide.cpp` y opciones de Release de `CMakeLists.txt` para Visual Studio. |
| Reducción de mallas (`engine/mbl_decimate`) | Michael Garland y Paul Heckbert. *Surface Simplification Using Quadric Error Metrics*, SIGGRAPH 1997 | (algoritmo; código propio) | Colapso de aristas con cuádricas para dar al motor una versión reducida de la malla. Implementación propia, sin código de terceros. |
| Medida de error de forma (`mbl_engine/metrics.py`) | Paolo Cignoni, Claudio Rocchini y Roberto Scopigno. *Metro: measuring error on simplified surfaces*, Computer Graphics Forum 1998 | (algoritmo; código propio) | Distancia entre superficies en los dos sentidos (Hausdorff), con la que se mide y se ajusta el retopo. |
| Punto más cercano (`engine/mbl_geom`) | Christer Ericson. *Real-Time Collision Detection*, Morgan Kaufmann 2004 (punto más cercano de un punto a un triángulo) | (algoritmo; código propio) | Búsqueda con jerarquía de cajas y varios hilos. Implementación propia, sin código de terceros. |

## Solo como referencia de investigación

Proyectos que hemos estudiado o probado y que **no** forman parte del plugin: no se distribuye ni se ejecuta nada suyo.

| Proyecto | Autores | Licencia | Qué queda |
|---|---|---|---|
| [QuadWild-BiMDF](https://github.com/cgg-bern/quadwild-bimdf) | QuadWild: Nico Pietroni, Stefano Nuvoli, Thomas Alderighi, Paolo Cignoni y Marco Tarini (SIGGRAPH 2021). BiMDF: Martin Heistermann, Jethro Warnett y David Bommes (SIGGRAPH 2023) | GPL-3 | Lo probamos como programa externo y lo descartamos (cifras en «Motores descartados» de `docs/notas-tecnicas.md`). El plugin ya no lleva sus binarios ni los ejecuta; sus artículos quedan como referencia de investigación. |
| Parveen (ArtStation, `parveencad4`) | Parveen | De pago y cerrado | Solo inspiración: hacer una vez el retopo y las UVs de las piezas repetidas y copiar el resultado a las demás. No se usa ni se copia nada suyo (es cerrado). |

## Bibliotecas usadas al compilar

| Proyecto | Autores | Licencia | Para qué |
|---|---|---|---|
| [Eigen](https://eigen.tuxfamily.org) | Benoît Jacob, Gaël Guennebaud y colaboradores | MPL 2 (algunas partes LGPL) | Álgebra lineal de Instant Meshes y QuadriFlow |
| [Boost](https://www.boost.org) (Graph) | Colaboradores de Boost | Boost Software License 1.0 | Flujo máximo en QuadriFlow |
| [LEMON](https://lemon.cs.elte.hu) | Grupo de Optimización Combinatoria Egerváry (EGRES) | Boost Software License 1.0 | Flujo de coste mínimo en QuadriFlow |
| [PCG32](https://www.pcg-random.org) | Melissa O'Neill; versión compacta de Wenzel Jakob | Apache 2.0 | Números aleatorios en Instant Meshes y QuadriFlow |
| Parallel Stable Sort | Intel Corporation | BSD 3 cláusulas | Ordenación en paralelo en Instant Meshes y QuadriFlow |
| [Intel TBB](https://github.com/oneapi-src/oneTBB) | Intel Corporation | Apache 2.0 | Hilos en Instant Meshes |
| [NanoGUI](https://github.com/wjakob/nanogui) | Wenzel Jakob | BSD 3 cláusulas | Interfaz de Instant Meshes (el plugin no la usa). Trae GLFW y NanoVG (licencia zlib) y GLAD (dominio público/MIT) |
| [RPly](http://w3.impa.br/~diego/software/rply/) | Diego Nehab | MIT | Lectura y escritura de archivos PLY en Instant Meshes |
| [half](https://half.sourceforge.net) | Christian Rau | MIT | Números de media precisión en Instant Meshes |
| dset (conjuntos disjuntos) | Wenzel Jakob | zlib | Agrupar vértices en Instant Meshes |
| MapleCOMSPS (derivado de MiniSat) | Jia Hui Liang y colaboradores; MiniSat: Niklas Eén y Niklas Sörensson | MIT | Resolutor SAT que trae QuadriFlow |

## Licencias junto a los ejecutables

Los ejecutables de `mbl_retopo_uv/bin` llevan al lado el texto de la licencia de cada proyecto:

- `LICENSE.txt`: Instant Meshes (BSD 3 cláusulas).
- `LICENSE-QuadriFlow.txt`: QuadriFlow (BSD 3 cláusulas).
- `mbl_decimate.exe` y `mbl_geom.exe` son código propio (licencia MIT, ver `LICENSE`); su fuente está en `engine/`.

El código del plugin es MIT, y ningún programa que se distribuye con él es GPL.

## Dentro de Maya

El plugin usa herramientas de Autodesk Maya: polyRetopo y Unfold3D. Son de Autodesk y vienen con Maya; el plugin no las distribuye.
