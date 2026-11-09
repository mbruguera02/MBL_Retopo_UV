# Historial de versiones

Cada versión lleva su número en el título de la ventana (`MBL Retopo UV 1.0.1`) y en `mbl_retopo_uv/__init__.py`.
Para actualizar: arrastra el `install.py` de la versión nueva a Maya (reemplaza la instalación anterior).

## 1.0.10 (9 de noviembre de 2026)

- **Motor nativo con red de seguridad:** si deja más de un 2 % de quads al revés (tela fina, modelos muy complejos como la caballera), se descarta, se avisa y el retopo se hace con Maya. Antes entregaba una malla retorcida y con agujeros.
- **Menos quads al revés en zonas finas:** al pegar la malla a la superficie, cada vértice solo va a triángulos que miran hacia su mismo lado (caballera: 271 → 204; prop: 70 → 54).

## 1.0.9 (9 de noviembre de 2026)

- **Sin limitador de RAM en ningún motor:** el Motor MBL ya no se para ni se niega por falta de memoria libre. Si va justo, avisa en el registro y sigue; irá más lento si Windows tiene que tirar de disco.

## 1.0.8 (9 de noviembre de 2026)

- **Motor nativo sin limitador de RAM:** ya no se para por falta de memoria libre. Con Maya abierto y poca RAM, el freno saltaba antes de empezar y el retopo acababa hecho con Maya.

## 1.0.7 (9 de noviembre de 2026)

- **Motor nativo (en pruebas):** opción nueva de «Motor», hecha desde cero en C++. Traza los loops desde los polos del campo de direcciones y rellena cada parche con una rejilla. Los polos caen donde los pondría un artista, y pone más caras donde la superficie se curva (cara, manos) y menos en las zonas planas. Humano a 15.000 caras en unos 13 s. Si falla, el retopo se hace con Maya y se avisa. Aún puede dejar caras dobladas en piezas duras con cantos vivos.
- **UVs con el motor nativo:** las costuras van solo por los loops de la malla. Las islas salen como rectángulos de rejilla, con bordes rectos y sin escalones; en el humano, del 53 % al 62 % de la textura aprovechada.
- Con el motor nativo se conservan las aberturas del modelo (ojos, boca): solo se cierran los agujeros que abre el retopo.

## 1.0.6 (5 de noviembre de 2026)

- **Cortes por ángulo:** «Ángulo de costura» ya solo corta cadenas largas, sin migajas. Sigue apagado por defecto (180°): a 90° empeoraba criatura y midnight. Para piezas duras, bájalo a 90°.
- **Enderezar:** las islas que son una rejilla sin polos quedan como una cuadrícula recta, como Straighten UV a mano.
- **La prueba del tablero:** el registro dice qué parte del área tiene cuadrados deformados; el estiramiento solo miraba el área.
- **Avisos** de escala no uniforme y de n-gons antes de desplegar.

## 1.0.5 (5 de noviembre de 2026)

- **Menos micro cortes:** una isla que se pisa se parte en dos trozos grandes en vez de pelarle lo que se pisa, se parten menos las islas, los restos pequeños se cosen a su vecina y se quitan los cortes en punta muy cortos. Se acepta algo más de estiramiento a cambio.
- **Cuadrícula ordenada:** cada isla se gira para que sus loops vayan lo más rectos posible en U y V.
- **Cortes más limpios:** las costuras siguen mejor los loops. Si el flujo de la malla no es limpio, los bordes siguen teniendo algo de dientes.

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
