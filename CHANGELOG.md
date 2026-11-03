# Historial de versiones

Cada versión lleva su número en el título de la ventana (`MBL Retopo UV 1.0.1`) y en `mbl_retopo_uv/__init__.py`.
Para actualizar: arrastra el `install.py` de la versión nueva a Maya (reemplaza la instalación anterior).

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
