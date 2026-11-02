# Instant Meshes MBL (nuestra versión del motor)

Copia del código oficial de [Instant Meshes](https://github.com/wjakob/instant-meshes) (última versión, 2019) con mejoras propias. El plugin usa el ejecutable compilado desde aquí, copiado en `mbl_retopo_uv/bin/Instant Meshes.exe`. El original sigue en esa carpeta como `Instant Meshes (oficial).exe`.

Sin las opciones nuevas da **exactamente** el mismo resultado que el oficial (comprobado con el águila: mismas caras, mismos polos, mismo error).

## Qué hemos cambiado

Todos los cambios están marcados con `MBL` en el código y recogidos en `mbl_changes.patch`.

- **Densidad adaptativa por curvatura** (`--adaptive <0..1>` y `--adaptive-ratio <r>`). El tamaño de cara deja de ser un único número y pasa a ser un valor por vértice: caras más grandes en zonas planas y más pequeñas en curvas. El número total de caras se mantiene.
  - `batch.cpp`: calcula la curvatura, la suaviza y la convierte en tamaño de cara.
  - `hierarchy.*`: guarda el tamaño en cada nivel de la jerarquía.
  - `field.cpp`: en cada arista usa la media de los tamaños de sus dos vértices.
  - `extract.cpp`: umbrales locales y criterio de "vértices espurios" normalizado (si no, borraba los vértices de las zonas densas).
- **Densidad pintada** (`--density <archivo>`). El archivo tiene un valor por vértice de la malla de entrada, en el orden del PLY: 1 = normal, 4 = cuatro veces más caras por unidad de área. La usa el pincel de detalle del plugin.
  - `batch.cpp` (`mbl_density_scale`):
    - convierte cada valor en tamaño de arista (1/√valor);
    - rellena los vértices que añade la subdivisión con la media de sus vecinos;
    - suaviza la transición entre zonas y normaliza para mantener el número de caras pedido.
  - El plugin pide las caras extra de lo pintado, para que lo no pintado no pierda detalle.
- **Direcciones guía** (`--orient <archivo>`). Una línea `dx dy dz peso` por vértice de entrada (orden del PLY). Cada iteración del campo de orientación tira hacia esa dirección con ese peso (0..1).
  - `batch.cpp` (`mbl_read_orientation`): proyecta cada dirección al plano tangente; los bordes (`-b`) tienen prioridad.
  - `hierarchy.cpp` / `hierarchy.h` (`mSoftConstraints`): en los niveles gruesos el peso es la media, no 1. Con peso 1 el campo queda clavado a direcciones con ruido.
  - El plugin calcula las direcciones de curvatura principal en `mbl_retopo_uv/orient.py`. En el humano no mejoraron al campo libre (más polos), así que van apagadas (`retopo_orient = 0`). El canal queda para guías dibujadas y anillos de tubos.
- `ext/nanogui/ext/glfw/CMakeLists.txt`: quitada una línea que CMake 4 ya no admite.

Resultados de la densidad adaptativa (0,4), al mismo número de caras: el error de forma medio bajó entre un 10 y un 25 % en la mayoría de pruebas (águila y humano). En algunos casos empeoró, y siempre añade polos (entre un 10 y un 40 % más). Por eso en el plugin está desactivada por defecto.

## Recompilar

Hace falta Visual Studio 2022 Build Tools (C++) y CMake 4. Desde esta carpeta:

```
cmake -S instant-meshes -B build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" -Wno-dev
cmake --build build --config Release --parallel --target InstantMeshes
```

El ejecutable queda en `build/Release/Instant Meshes.exe`. Cópialo a `mbl_retopo_uv/bin/` para que lo use el plugin.

Para recuperar los cambios en un clon limpio: `git apply mbl_changes.patch` dentro de `instant-meshes`.

---

# mbl_geom (punto más cercano)

Programa propio en `engine/mbl_geom` (C++, sin dependencias). Servidor de consultas de punto más cercano sobre una malla de triángulos: jerarquía de cajas (BVH) y varios hilos, exacto (mismas distancias que la rejilla de Python, hasta 1e-14). El núcleo lo usa para el grosor, el ajuste, la proyección y la medida de error; sin él todo sigue funcionando en Python, unas 800 veces más lento.

```
cmake -S mbl_geom -B build-geom -G "Visual Studio 17 2022" -A x64
cmake --build build-geom --config Release
copy build-geom\Release\mbl_geom.exe ..\mbl_retopo_uv\bin\
```

Protocolo por stdin / stdout, en binario: `M` (nº de puntos, nº de triángulos, puntos, triángulos) carga la malla; `Q` (nº de consultas, puntos) devuelve los puntos más cercanos, las distancias y el triángulo de cada uno; `X` termina. Un proceso por malla (`closest.NativeGrid`); al soltarlo se cierra.

---

# mbl_decimate (reducir la malla)

Programa propio en `engine/mbl_decimate` (C++, sin dependencias). Reduce una malla de triángulos por colapso de aristas con cuádricas (Garland y Heckbert, SIGGRAPH 1997). El plugin lo usa para dar a QuadriFlow una versión de unos 100.000 triángulos en vez de los 528.000 del águila: el motor pasa de 43 s a 10 s con el mismo error.

```
cmake -S mbl_decimate -B build-decimate -G "Visual Studio 17 2022" -A x64
cmake --build build-decimate --config Release
copy build-decimate\Release\mbl_decimate.exe ..\mbl_retopo_uv\bin\
mbl_decimate entrada.obj salida.obj caras_objetivo [peso_borde]
```

Conserva la topología: un colapso solo se hace si cumple la condición de enlace (no deja aristas con más de dos caras ni pajaritas), no da la vuelta a ninguna cara y no mueve los bordes abiertos (llevan cuádricas extra que los sujetan). El águila reducida tiene los mismos 3 bordes abiertos que el original.

---

# QuadriFlow MBL (segundo motor)

Cambio del 29/10: con `-density` ya no se mezcla la densidad de cada vértice con la de sus vecinos (5 vueltas del mínimo en `Parametrizer::Initialize`). Con un campo propio ya viene suavizado y esa mezcla ensanchaba la zona densa según la resolución de la entrada, así que el número de caras no se podía predecir.

Copia de [QuadriFlow](https://github.com/hjwdzh/QuadriFlow) (Huang, Zhou, Nießner, Shewchuk y Guibas, licencia BSD de 3 cláusulas) en `engine/quadriflow`, compilado en `engine/build-quadriflow/Release/quadriflow.exe`. Esa carpeta no va en git: se descarga del repositorio original y se le aplica `quadriflow_mbl_changes.patch`.

Compilar necesita Boost (cabeceras) y Eigen 3.4 en `engine/deps` y Visual Studio 2022:

```
cmake -S quadriflow -B build-quadriflow -G "Visual Studio 17 2022" -A x64 "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" -Wno-dev -DEIGEN_INCLUDE_DIR=.../deps/eigen-3.4.0 -DBoost_INCLUDE_DIR=.../deps/boost_1_86_0
cmake --build build-quadriflow --config Release --parallel --target quadriflow
```

## Qué hemos cambiado

- **Densidad por vértice** (`-density <archivo>`). Un valor por vértice del OBJ de entrada, en su mismo orden: 1 = normal, 4 = cuatro veces más caras por área. Se convierte en tamaño de arista (1/√valor) y se vuelca en el campo de escala `S` que QuadriFlow ya usaba al optimizar posiciones (`Optimizer::apply_scale_field`, que también lo propaga a los niveles gruesos). QuadriFlow no mantiene el total de caras: el plugin pide menos de base (dividiendo por la densidad media) y reintenta una vez si se desvía más del 20 %.
- **Arreglo de un error de la biblioteca** (`subdivide.cpp`): al subdividir la malla calculaba el valor de los vértices nuevos como `0,5 * (rho[v0], rho[v1])`, un operador coma que vale `0,5 * rho[v1]`. Con un valor por vértice cada subdivisión lo dividía por la mitad y el resultado tenía 27 veces más caras. Ahora es la media.
- `CMakeLists.txt`: las opciones de Release se fijan solo fuera de Visual Studio, porque si no el enlace fallaba.

Con densidad constante 1 da exactamente el mismo resultado que sin ella (4.433 caras en el humano); con 2 y 4 da 9.663 y 19.376.

---

# Antes de repartir los ejecutables

Visual Studio deja dentro de cada `.exe` la ruta de las carpetas donde se compiló (en los mensajes de error de las bibliotecas y en la del archivo `.pdb`). Si compilaste dentro de tu carpeta de usuario, esa ruta lleva tu nombre. Después de compilar y copiar los `.exe` a `mbl_retopo_uv/bin`, ejecuta:

```
python engine/scrub_paths.py
```

Cambia cada ruta por otra de la misma longitud (`C:\mbl_compilado_____\engine\...`); el programa funciona igual. Se puede pasar la ruta de un `.exe` concreto en vez de revisar toda la carpeta `bin`.
