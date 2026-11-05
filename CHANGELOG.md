# Historial de versiones

Cada versión lleva su número en el título de la ventana (`MBL Retopo UV 1.0.1`) y en `mbl_retopo_uv/__init__.py`.
Para actualizar: arrastra el `install.py` de la versión nueva a Maya (reemplaza la instalación anterior).

## 1.0.4 (5 de noviembre de 2026)

- **UVs:** ya no fallan con mallas que tienen caras sin UVs (fallo de la 1.0.3).
- **UVs:** las pocas caras que seguían solapadas tras los intentos se separan en islas propias; prop y midnight ya salen en verde.
- Verificada con las suites completas y el banco de 5 modelos a 5.000 caras, que faltaban en la 1.0.3.

## 1.0.3 (4 de noviembre de 2026)

- **Crash con mallas densas:** Unfold3D rompía Maya al pedirle que partiera islas (comprobado con 60.000 caras). El plugin ya no se lo pide.
- **Divisor de islas propio:** la malla se parte en zonas casi planas y luego solo se vuelven a partir las islas que siguen estirando. Medido a 20.000 caras: caras estiradas más de 1,5x de 8-9 % a 3-4 % en humano y midnight, la mitad de islas y sin cortes en zigzag. Aprovecha algo menos la textura en 0-1 (46-52 % frente a 60-62 %).
- **UDIM:** las islas se reparten según lo que ocupan de verdad y se parten las que frenan la densidad: 48-54 % de cada tile con 3 tiles, frente al 32-37 %.
- **Error E62044 de Unfold3D:** ya no aborta la pasada; y unas pocas caras solapadas en UDIM avisan en vez de hacerla fallar.
- **«Ver cuadrícula»** usa una textura de casillas numeradas (ver `CREDITS.md`).
- Publicada sin terminar la verificación completa: faltaban las suites y el banco de 5 modelos a 5.000 caras con el divisor nuevo.

## 1.0.2 (3 de noviembre de 2026)

- **UDIM:** si tras empaquetar el margen entre islas queda por debajo del padding, se reempaqueta con más separación (hasta 2 veces).
  No se ha podido comprobar con el retopo denso del usuario (no se reprodujo el margen de 0,5 px con mallas de prueba).
- Sigue pendiente limitar la fragmentación en islas pequeñas de las mallas densas.

## 1.0.1 (3 de noviembre de 2026)

- **Motor por defecto: Maya.** El Motor MBL queda como experimental; QuadWild se ha quitado. «Automático» e Instant Meshes siguen.
- **Ventana:** lo principal a la vista (motor, caras, Recomendar, resolución) y el resto en «Avanzado»; botón «Valores por defecto»;
  botón «Ver cuadrícula» (material de cuadros para ver el estiramiento).
- **Retopo:** ya no deja geometría no-manifold (fallaban las UVs en modelos como el Midnight); conserva materiales por caras,
  pivote, transformación y grupo del original; no depende de las unidades del modelo; las piezas repetidas se hacen una sola vez.
- **Rehacer (Ctrl+Y)** tras deshacer un retopo ya no cierra Maya.
- **Semáforo:** el del retopo y el de las UVs usan los jueces de calidad del núcleo; ya no sale verde con defectos.
- **UVs:** el paso es mucho más rápido (humano de 33 s a 10 s); margen entre islas automático según la resolución; bordes entre
  materiales como costuras; los tubos cortados a lo largo se alinean para que el corte case; pasada atómica (si falla, la malla
  vuelve a como estaba); corregido el error `index … out of bounds` con mallas de más de ~46.000 caras.
- **Motor MBL:** de 40 a 60 % menos de tiempo con la misma calidad.
- **Sin activar por defecto:** corregir islas estiradas (no cumplió el banco), densidad de texel común entre mallas.
- **Conocido:** UDIM con mallas de ~200.000 caras es muy lento y puede dejar islas fragmentadas y con poco margen.

## 1.0.0 (2 de noviembre de 2026)

Primera versión pública: limpieza, retopo (Maya, Instant Meshes, Motor MBL) y UVs para Substance Painter.
