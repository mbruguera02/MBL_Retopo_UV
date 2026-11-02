# Motor MBL: retopo por anillos — diseño

Fecha: 2026-10-30. Estado: **pendiente de aprobación**. Lo marcado como *(mi decisión)* lo he elegido yo, no se ha hablado.

## Intención y por qué

El Motor MBL tiene que ser mejor que polyRetopo de Maya. Para el usuario, "mejor" no es el menor error de forma sino lo que se ve y lo que facilita las UVs:

- Si se coge un vértice o una arista, **el loop que pasa por él se cierra del todo**. Con loops perfectos el corte de UV sale limpio y hay más margen de maniobra.
- Quads de **tamaño parejo** que siguen **el mismo cauce** (paralelos).
- Sin pliegues ni caras dobladas, sin agujeros que no estaban en el original.
- Parecido al original lo máximo posible (la forma es un límite que no se rompe), pero a igual forma gana la malla más ordenada.

El motor actual (QuadriFlow con ajustes) mide mejor en error de forma pero peor en lo que se ve, medido con `quality.py`: humano, índice de fealdad 4,2 frente a 1,0 de Maya; aristas en loops cerrados 2-8 % frente a 14-24 %. QuadriFlow hace espirales que no cierran y su campo es menos regular. Retocar su resultado no arregla eso: hay que construir la malla con los anillos como base.

## Metas medibles (en `quality.py`, sobre humano, criatura y los modelos de prueba)

1. Aristas en loops cerrados: claramente por encima de Maya (objetivo inicial: más del 70 %); los anillos principales (extremidades, cuello, tronco) cierran siempre.
2. Quads deformados < 2 %, saltos de tamaño entre caras vecinas < 3 %.
3. Cero caras dadas la vuelta o dobladas y cero agujeros nuevos; solo quads salvo los que fuerce la paridad.
4. Simétrico con bucle central cuando el modelo lo es (reflejo exacto).
5. Error de forma igual o mejor que el de Maya.
6. **Tiempo máximo por modelo: 5 minutos** *(dicho por el usuario)*, con aviso en la ventana. Sin límite para construirlo.
7. El plugin nunca se queda sin resultado: si el motor falla o su nota de calidad es peor, se usa el resultado de Maya o QuadriFlow (el modo Automático ya compara por calidad).

## No metas

- Perseguir el menor error de forma a costa de quads feos (error que cometí antes).
- Malla para animación facial o musculatura (flujo semántico): no se pide.
- Quads sin polos: imposible; solo se pide que los anillos principales cierren.

## Restricciones

- Núcleo `mbl_engine` en Python con numpy y C++, sin Maya y sin scipy; Maya solo como adaptador.
- Sin código GPL en el núcleo (QuadWild solo como programa externo). Se acreditan todos los autores (`CREDITS.md`).
- Plugin de Maya 2026 en Windows; QuadriFlow sigue como pieza externa de respaldo.
- Sin Unfold3D en las pruebas del núcleo: las UVs se comprueban con el plugin en Maya.

## Enfoque *(mi decisión, probado en parte)*

**Anillos primero.** Una curva de nivel de una función sobre una superficie es cerrada por construcción, así que el flujo en anillos sale de ahí y no de suerte.

1. **Función base.** Distancia (geodésica o armónica) desde una raíz (pelvis o centro del modelo; una por pieza suelta). Sus curvas de nivel, a paso igual al tamaño de quad, son los anillos. *Experimento (desechable, sin código en el proyecto) sobre el humano: 232 anillos, 0 abiertos, 100 % de las aristas en curvas cerradas, 27 puntos donde un anillo se parte (hombros, caderas, dedos).*
2. **Segunda familia.** Las líneas a lo largo de cada tramo, ortogonales a los anillos (campo de dirección propio, `field.py`, alineado con la distancia). Cada anillo recibe m puntos repartidos con paso igual.
3. **Celdas de transición** donde un anillo cambia de m a m' puntos entre niveles (reducción o aumento de densidad). Catálogo canónico: 2:1, 3:1, 4:2, 5:1 y 6:2. Un texto externo propone patrones y polos para cada una, pero su regla de balance de polos es incorrecta cuando la frontera cambia de tamaño. **Cada celda se construye en código y se verifica** (solo quads, valencias, característica de Euler) antes de usarse.
4. **Ramas** (un anillo que se parte: hombro, cadera, dedos): plantillas con pares de polos 3-5, también verificadas.
5. **Pegado al original** con las reglas ya hechas (`guarded_project`, ajuste que no empeora quads), simetría por mitad con bucle central, y selección por `quality.py`.
6. **Respaldo:** QuadriFlow y Maya, con elección por calidad.

## Orden de trabajo y criterio de abandono

1. Tubo y tubo con rama (sintéticos): anillos + celdas de transición verificadas. Salida: quads limpios, anillos cerrados.
2. Humano: medir contra Maya con `quality.py`.
3. Criatura (528.554 triángulos, muchas ramas): tiempo y calidad.
4. Conectar al plugin como motor y al Automático.

**Criterio de abandono:** si tras 1 y 2 los anillos cierran pero las transiciones dejan más defectos que Maya, se deja QuadriFlow como base y solo se aplica la limpieza de loops. Se avisa antes de seguir.

## Banco de pruebas

Humano y águila (ya están), `criatura.fbx` (entrada mala), y **un OBJ de `goodretopo.Fbx`** (buena topología de referencia; no se pudo importar por FBX en Maya sin interfaz). Se añaden los modelos del usuario que lleguen: esculpido, hard-surface y low-poly en triángulos.

## Lo que se deja al constructor

Estructuras de datos, C++ frente a numpy en cada pieza, elección de raíz, paso exacto de los anillos, cómo parametrizar la segunda familia y la forma concreta de cada plantilla (siempre verificada).

## Preguntas abiertas para el usuario

- Dónde dejar el OBJ de `goodretopo`.
- Los modelos de prueba adicionales.
- Si el 70 % de aristas en loops cerrados le parece buen primer objetivo.

## Resultado de la comprobación del criterio de abandono (30/10)

Cuerpo del humano a 5.000 caras (arista objetivo 2,32), con `rings.py`, `cells.py` y `ringmesh.mesh_from_rings` (tareas 1 a 5), relajado y pegado al original:

| | Anillos | Caras | Quads deformados | Saltos de tamaño | Polos por 100 caras | Aristas en loops cerrados | Tiempo |
|---|---|---|---|---|---|---|---|
| Motor por anillos (sin retopo completo) | 208, todos cerrados | 5.965 | 39,6 % | 9,8 % | 8,8 | 23,7 % | 7,7 s |
| Maya | n/d | 4.838 | 0,8 % | 0,2 % | 3,9 | 24,1 % | 3 s |
| QuadriFlow + ajustes | n/d | 5.489 | 4,4 % | 0,9 % | 2,6 | 6,6 % | 13 s |

- Los anillos cierran (100 % de los 208) pero las transiciones y ramas dejan bastante más defectos que Maya: **se cumple el criterio de abandono** y se para antes de la tarea 6.
- **La meta de más del 70 % de aristas en loops cerrados no se puede alcanzar** con flujo en tubos: solo cierran los anillos, que son como mucho la mitad de las aristas (las líneas a lo largo acaban en polos y tapas). Maya llega al 24 % y los anillos al 23,7 %. La meta debe reformularse: anillos principales cerrados (extremidades, cuello, tronco), no un porcentaje de aristas.
- Palancas que no se han probado y podrían bajar los polos y los deformados: tolerancia de recuentos mayor, relajado propio con restricciones de anillo, alinear mejor las fases en las ramas, y suavizar la distancia antes de sacar los anillos.

## Resultado del relajado a quads cuadrados (30/10)

Tras abandonar el motor por anillos se probó atacar los quads deformados del resultado de QuadriFlow, que existen ya a la salida del motor (4,3 % en el humano) y que ningún paso posterior crea. Causa: **9 de cada 10 quads deformados tocan un vértice que el relajado no mueve** (borde del modelo o arista marcada: con 5.000 caras, el 35 % de los quads toca alguno). Soluciones, en `quadopt.py`:

- `regularize`: cada quad propone el cuadrado que mejor encaja con él (mínimos cuadrados, tamaño acercado al de sus vecinos) y cada vértice va a la media de las propuestas de sus quads; se pega al original en cada pasada y se frena (medio paso o quieto) cualquier movimiento que gire la normal de un quad, lo aplaste o **cree una pareja de quads doblados**.
- `fix_deformed`: suelta los vértices de los quads que siguen deformados y los de 2 filas alrededor, aunque estén en aristas marcadas.

| 5.000 caras, QuadriFlow, 1 variante | Deformados | Saltos de tamaño | Volteadas / dobladas |
|---|---|---|---|
| Humano, antes | 4,36 % | 1,46 % | 75 / 70 |
| Humano, ahora | 3,53 % | 0,99 % | 57 / 41 |
| Águila, antes | 7,98 % | 1,52 % | 184 / 161 |
| Águila, ahora | 5,57 % | 0,82 % | 170 / 101 |
| Criatura, antes | 11,07 % | 3,41 % | 187 / 226 |
| Criatura, ahora | 7,96 % | 3,35 % | 203 / 186 |

Índice de fealdad del mejor de 3 variantes: humano 4,18 a 2,95 (Maya 1,02), águila 9,26 a 8,60 (Maya 5,81). El error de forma no cambia (humano 0,117 %, águila 0,123 %).

Dos protecciones evitan que cuadrar plegue las láminas finas: `regularize` y `fit` no dejan que un movimiento convierta en doblada una pareja de quads vecinos (sin esto las dobladas del águila subían de 161 a 229 y las de la criatura de 226 a 323; con ello bajan, y a la salida del motor el ajuste de `fit` ya no suma pliegues). Además `retopo` calcula las dos terminaciones (relajado por centroides y cuadrados) y se queda con la de menor índice de fealdad.

Probado y descartado: pesar las propuestas por planitud del quad (empeoró deformados y dobladas), congelar las zonas más finas que una arista (los deformados del humano subieron a 3,9 %, de las manos), y deslizar por el plano los vértices del corte de simetría (el humano mejoró algo, el águila empeoró y no se encontró la causa). El águila es casi toda lámina fina: sus «volteadas» y «dobladas» (157 y 75 en Maya) son en buena parte un artefacto de medir una malla de una capa contra un original de dos capas.

Lo que no cambia: polos y loops cerrados. Eso viene del campo de QuadriFlow, no de la posición de los vértices; la palanca que queda es un extractor de campo propio.

## Prueba descartada: campo de anillos como pista para QuadriFlow (30/10)

Hipótesis: los loops en espiral de QuadriFlow vienen de que su campo de direcciones gira respecto al eje de cada miembro. Se calculó un campo propio, la tangente a las curvas de nivel de la distancia desde la raíz (Eikonal por triángulos, mucho menos sesgada hacia las diagonales que la distancia por aristas), y se pasó al fork como restricción blanda por vértice (`-orient`, con propagación blanda a los niveles gruesos).

Resultado, humano a 5.000 caras, 8 semillas por ajuste:

| | Deformados (mediana) | Polos por 100 caras | Aristas en loops cerrados (mediana) |
|---|---|---|---|
| Sin pista | 4,0 % | 4,5 | 6,9 % |
| Con pista (peso 0,4) | 5,7 % | 5,7 | 3,5 % |

Con peso 0,7 los deformados suben a 8,6 % y con 1,0 a 38,6 %. En una cápsula sencilla (cilindro con casquetes), donde el campo de anillos es perfecto, tampoco: sin pista 8 polos y loops en espiral; con pista 10 a 14 polos y los loops siguen sin cerrar.

Causa: la espiral no está en el campo de direcciones sino en la **parametrización de posiciones**. Dar la vuelta a un tubo suma una traslación entera de la rejilla; si tiene componente a lo largo del eje, la rejilla es una hélice (una dislocación de tornillo) y nada en QuadriFlow la penaliza. Para cerrar los anillos hay que imponer traslación cero en los ciclos no contráctiles, que es una restricción del mapa de rejilla entera y no se arregla desde el campo. Se retiró todo (`-orient` del fork, `flowfield.py`); el fork vuelve a ser el de `quadriflow_mbl_changes.patch`. El mismo número de caras tampoco es estable con esta restricción (el pedido de 3.250 dio de 2.361 a 3.559).

Consecuencia: los loops cerrados solo salen de una construcción que los lleve por diseño (el motor por anillos) o de una restricción de ciclos en el mapa de rejilla (investigación aparte).

## Medida de la malla de referencia hecha a mano: goodretopo (30/10)

`geo/goodretopo.obj`: 224.736 quads, una pieza, 6 bordes abiertos (malla densa de producción, no de 5.000 caras).

| | goodretopo | Maya (humano, 4.838 caras) | Motor MBL (QuadriFlow) 5.131 / 12.458 / 30.378 caras |
|---|---|---|---|
| Polos por 100 caras | 0,50 | 3,9 | 4,5 / 3,0 / 1,8 |
| Vértices regulares | 99,5 % | 96,0 % | 95,3 % (a 5.000) |
| Aristas en loops cerrados | **76,7 %** | 24,1 % | 9,7 % / 7,3 % / **3,8 %** |
| Longitud media de loop | 1.487 | 50 | 150 / 183 / 390 |
| Saltos de tamaño entre vecinas | 0,04 % | 0,2 % | 1,0 % (a 5.000) |
| Quads «deformados» | 6,75 % | 0,8 % | 3,5 % (a 5.000) |

Lecturas:
- **El cierre de loops no es solo cuestión de pocos polos.** QuadriFlow baja de 4,5 a 1,8 polos por 100 caras al subir la resolución y el cierre *baja* de 9,7 % a 3,8 %, con loops cada vez más largos: espirales. La malla a mano cierra 76,7 % con 0,5 polos por 100 caras.
- **Los «quads deformados» no distinguen una malla buena.** La de referencia tiene más (6,75 %): el artista usa quads alargados a lo largo de los miembros, que nuestro umbral de proporción (3) marca como malos. El trabajo de quads cuadrados mejora el aspecto pero no el flujo.
- **Lo que sí la distingue:** loops que cierran y tamaño uniforme (0,04 % de saltos).
- Sin resolver: qué cierre es alcanzable a 5.000 caras (la referencia tiene 45 veces más).

### Referencia a menos caras: quitar loops de goodretopo (30/10)

Para saber qué cierre es alcanzable con pocas caras se coarseó `goodretopo` quitando bucles duales enteros (cada tira cerrada de quads se colapsa en una fila de aristas: los anillos que cerraban siguen cerrados y los pares de polos a ambos lados se anulan), de menor a mayor área. Se detiene en **36.206 caras**: quedan 94 tiras y 80 se cruzan a sí mismas, que no se pueden colapsar sin degenerar.

| | Caras | Polos por 100 | Aristas en loops cerrados |
|---|---|---|---|
| goodretopo | 224.736 | 0,50 | 76,7 % |
| goodretopo coarseado | 36.206 | 1,39 | **73,3 %** |
| Motor MBL (QuadriFlow) | 30.378 | 1,80 | 3,8 % |
| Maya | 4.838 | 3,9 | 24,1 % |

Con una densidad de polos parecida (1,4 frente a 1,8 por 100), la estructura hecha a mano conserva el 73 % de aristas en loops cerrados y QuadriFlow el 4 %. Los polos no explican la diferencia: la explica el flujo. (La calidad de forma del coarseado, 24 % de deformados, es producto del método y no sirve de referencia.) Los dos tercios de las tiras de una malla real se cruzan a sí mismas, así que el flujo bueno no es «un anillo por miembro» sino tiras largas que envuelven el cuerpo.

## Simetría del Motor MBL: costura central, red de seguridad y limpieza (30/10)

**Causa de los fallos de la costura.** Con simetría, los fallos nacían ya en el motor, antes de unir las mitades: la tapa de triángulos con que se cerraba el corte para QuadriFlow deformaba los quads de la franja central. Medido en la criatura: 45-59 caras volteadas y 64-112 dobladas en la franja de 3 aristas (unas 10 veces más que sin simetría).

**Arreglo.** QuadriFlow admite mallas abiertas con `-boundary`: sus quads quedan alineados con el borde y los vértices del corte caen sobre el plano. Se quita la tapa (`cap_cut`, `cap_faces_mask`). `-sharp` además empeoró (más triángulos), así que no se usa.

| Con simetría, 5.000 caras (3 semillas) | Criatura: fealdad | Criatura: franja volteadas / dobladas | Humano: fealdad |
|---|---|---|---|
| Con tapa (antes) | 13,5 a 14,2 | 45-59 / 64-112 | 2,95 a 4,66 |
| `-boundary` (ahora) | 6,3 a 7,7 | 0-13 / 0-8 | 1,14 a 3,52 |

Además el número de caras cae en el objetivo (la criatura daba 3.619 de 5.000 pedidas con la tapa), se tarda la mitad (12 s frente a 24) y el águila deja de salir con bordes abiertos.

**Producto.** La simetría viene apagada por defecto (ajustes versión 4). Con ella encendida, el Motor MBL prueba variantes con y sin simetría (una de cada tres, mínimo una) y entrega la simétrica solo si no deja más fallos visibles que la mejor sin ella (sin tolerancia desde la prueba a 25.000 caras: allí la simetría dejaba 3-5 veces más caras volteadas; con calidad alta, 3 de las 9 variantes van sin simetría, alternadas con las simétricas); si no, entrega la asimétrica y avisa.

**Limpieza.** Se retiraron el motor por anillos (`rings.py`, `ringmesh.py`, `cells.py`), el campo de orientación propio (`field.py`), el campo de tamaño (`sizefield.feature_density`) y el limpiador voraz de polos antiguo: 497 líneas menos entre código y pruebas. Siguen en el historial de git (commit anterior a 25dc091).

### Criatura a 25.000 caras: qué son los fallos y por qué no se reparan (30/10)

Con simetría automática y calidad alta (8 variantes, unos 5 minutos) quedaban caras negras en el vídeo de Maya. Medido en la criatura reproducida (24.136 caras, una variante):

- **Volteadas 4,6 % y dobladas 2,8 %.** Winding coherente (0 aristas con sentido contrario): no es un error de orientación.
- **Dos clases de «volteada».** El parche mayor (482 caras) es suave y coincide con sus vecinas (dot medio 0,96, solo se opone a la normal del original): una capa fina del original, no un pliegue. Los pliegues reales son pares espejo de ~70 caras con el 30 % del borde opuesto a las vecinas y lengüetas de 16 caras invertidas (82 % del borde opuesto).
- **Sin simetría, un tercio de los fallos.** Con simetría 3,2–4,6 % volteadas y 1,3–1,7 % dobladas (fealdad 5,6–9,1); sin ella 0,3–0,9 % y 0,4–1,0 % (1,1–3,5).
- **Reparación local probada y descartada.** Aplanar cada parche plegado más 2 anillos de caras sanas, repartir los vértices interiores como media de sus vecinos (incrustación de Tutte) y pegarlos al original: dobladas 668 → 475 (-29 %), volteadas 1104 → 979 (-11 %), con deformados 10,0 → 10,7 % y error medio +5 %. Satura a la cuarta ronda y cuesta ~15 s por ronda en Python. Una difusión simple sin plano ni borde fijo empeoró. No se integra: la mejora es menor que la de elegir una variante sin simetría.

### Tras la segunda prueba del usuario en Maya (30/10)

Criatura, 25.000 caras, simetría automática, calidad alta: 3 min 17 s y **solo dos fallos visibles**, una esquirla junto a la nariz y las muñecas.

- **Muñecas.** Son un tubo de unos 1,3 aristas de radio (anillo de ~8 quads): ahí QuadriFlow pliega. En las pasadas sin simetría la zona sale limpia; aparece en variantes simétricas sueltas, y como son ~0,1 % de las caras, la nota global no las penaliza.
- **Densidad en tubos finos (`thin_features`, epr 3, máx. 4).** En la criatura baja la fealdad media de 2,6 a 1,9 sin simetría (de 6,0 a 4,8 con ella), baja el error máximo y acorta el tiempo. En el humano empeora (6,4 y 6,3 en dos semillas frente a 1,1-1,4) y en el águila no aporta. Por eso solo la usan las variantes de la segunda ronda de tres (4ª a 6ª): candidatas de más y la elección se queda con la mejor.
- **Resultado de la comprobación de principio a fin (calidad alta):** gana una variante sin simetría con densidad en tubos finos (fealdad 1,77; volteadas 0,52 %, dobladas 0,89 %, deformados 1,07 %) y el plugin avisa de que la simetría no se aplicó. Una variante falló sola («no devolvió ninguna malla») y se descarta sin más.
- **Dato lateral.** Con la mitad abierta (`-boundary`) el humano sale a 5.000 caras con **12-34 % de aristas en loops cerrados** (Maya 24 %; antes del cambio, ~10 %) y 125-172 polos (Maya 190).

### Puntas de las alas de la criatura: el engordado 2,0 sin simetría (2/11)

El usuario vio las puntas de las plumas «rotas» en Maya. Reproducido con el plugin entero en Maya sin interfaz (`pipeline.run`, 25.000 caras, calidad alta, simetría automática): el canto de cada pluma (lámina más fina que una arista) salía como una tira en zigzag de caras dobladas (4-6 % de las caras de las alas frente al 0,8 % del resto). La variante ganadora de calidad alta era sin simetría con engordado 1,0 y densidad en tubos finos (fealdad 1,77). Con engordado 2,0 sin simetría las alas salen limpias (dobladas 1,0 %, volteadas 0,5-0,8 %) y la fealdad es 1,18 (error medio 0,030 %, máximo 1,01 %), pero las variantes sin simetría solo copiaban los engordados 1,0 y el de tubos finos. Ahora copian primero 1,0 (el mejor en el humano), luego 2,0 y luego tubos finos. Comprobado: gana la variante sin simetría con engordado 2,0 (fealdad 1,18, 157 s con 9 variantes).

No sirvió dejar los vértices de las láminas sobre la superficie engordada en lugar del original: los pliegues ya vienen del motor.

### Cabeza «girada»: pista de curvatura para QuadriFlow, descartada (2/11)

El usuario ve la rejilla de la cabeza (pico) torcida respecto al eje. Medido con el plugin entero en la criatura (25.000 caras, variante ganadora sin simetría): las aristas de la cabeza respecto a la dirección local de curvatura salen alineadas (a menos de 15° o más de 75°) el 60-65 %, en diagonal el 10-15 %. La medida respecto al eje no sirve (el pico es un cono y las generatrices van a ~20° del eje).

Se restauró `-orient` en el fork (restricción blanda por vértice, propagada a los niveles gruesos) y se probó con las direcciones de `orient.curvature_directions` (en la cabeza el 97-100 % rodea el eje). Comprobación directa en un cilindro con 45° impuestos: el 100 % de las aristas salen a 40-50°; la entrada funciona. En la criatura, 3 semillas con peso 1,0: alineación de la cabeza 64-72 % (+4 puntos), **polos 1.190-1.379 frente a 787-846** y fealdad 1,12-4,49 frente a 1,25-1,50. Con pesos menores no cambia nada. Se retira (Python y fork); el fork vuelve a coincidir con `quadriflow_mbl_changes.patch`.

Conclusión: la torsión de la rejilla no es un giro del campo de direcciones sino de la parametrización de posiciones (misma causa que las espirales: un desplazamiento entero a lo largo del eje), y no se corrige con pistas de dirección. Dos pruebas con pistas distintas (distancia geodésica, curvatura) dan lo mismo.

