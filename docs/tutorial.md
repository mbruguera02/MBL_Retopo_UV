# Tutorial de MBL Retopo UV

Guía práctica para usar el plugin en Maya 2026, con ejemplos rápidos de para qué sirve cada apartado y una parte dedicada a las **guías de flujo** y al **pincel de detalle**.

> Escrito para la versión de principios de noviembre de 2026 (con el analizador de caras, el aviso de piezas finas y la protección de memoria) y contrastado con el código de la ventana de esa versión. Si algún botón no coincide, manda lo que diga la ventana. Para instalar el plugin, mira el [README](../README.md).

---

## 1. Qué hace, en 30 segundos

Tienes un modelo «sucio» (de IA, escaneo o sculpt) con miles de triángulos desordenados. El plugin hace tres cosas, en este orden:

1. **Limpieza:** arregla errores del modelo que romperían los pasos siguientes.
2. **Retopo:** crea una malla nueva de cuadrados (quads) ordenados, con las caras que pidas, pegada a la forma del original.
3. **UVs:** abre esa malla en un plano para poder pintarla o texturizarla.

El resultado es una malla nueva que termina en `_retopo`. **El original no se toca**: queda oculto en la escena. El `_retopo` conserva lo que tenías puesto en él: sus materiales (si tiene varios, cada zona del `_retopo` recibe el de la zona más cercana del original), el pivote, la posición/rotación/escala y el grupo donde estaba, así que cae justo encima del original.

---

## 2. Antes de empezar

- **Guarda la escena.** Un solo Ctrl+Z deshace todo el proceso, pero es mejor tener copia.
- **Selecciona el modelo original**, no un retopo anterior. *Limpiar*, *Retopo*, *UVs* y *Todo* trabajan sobre lo que tengas seleccionado. Solo los botones de guías y de pincel de detalle, si seleccionas un retopo, buscan su original y lo usan.
- **Memoria:** con modelos muy grandes o muchas caras, cierra programas pesados. El plugin vigila la memoria y se para solo si va justo, pero cuanta más libre, más rápido.

---

## 3. Tu primer retopo en 5 pasos

1. Selecciona el modelo.
2. Abre **MBL > MBL Retopo UV**.
3. Pulsa **Recomendar caras** y pon esa cifra en **Caras objetivo**.
4. Pulsa **Todo: limpiar → retopo → UVs**.
5. Mira el **semáforo** del panel *Estado*. Verde = listo. Amarillo o rojo = lee la línea **→** que sale debajo: te dice qué tocar.

> El **Motor** viene en *Maya (polyRetopo, recomendado)*: no hace falta cambiarlo. A la vista tienes lo principal (**Motor**, **Caras objetivo**, **Recomendar caras** y **Resolución textura**, en *Ajustes principales*), los botones, el semáforo y el log. Todo lo demás está plegado en el grupo **Avanzado**: pulsa su título para abrirlo.

> **Ejemplo:** criatura de IA de 500.000 triángulos. «Recomendar caras» dice 17.000. Pones 17.000, pulsas *Todo* y en unos minutos tienes una malla de ~17.000 quads con UVs, lista para Substance.

---

## 4. Qué hace cada apartado (con ejemplos rápidos)

### 4.1. Limpieza

Arregla el modelo antes del retopo. Casi nunca hace falta tocar nada. Está en el grupo plegado **Avanzado**, apartado *Limpieza*.

| Opción | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Borrar historial** | Quita el historial de construcción del modelo | Un modelo con 40 operaciones de modelado en el historial: se queda limpio y más ligero |
| **Congelar transformaciones** | Fija posición, giro y escala a cero / uno | Modelo importado con escala 0,01: evita que el retopo salga diminuto o girado |
| **Fusionar vértices** (+ distancia) | Une vértices que están en el mismo sitio | Un modelo de IA con costuras abiertas que parecen cerradas: las suelda |
| **Conformar normales** | Hace que todas las caras miren hacia fuera | Zonas que se ven negras en el visor porque están del revés |
| **Reparar no-manifold** | Separa geometría «imposible» (aristas compartidas por 3 caras o más) | Si las UVs dan el error *non-manifold*, marca esto y pulsa *Limpiar* |
| **Analizar y seleccionar problemas** | Busca errores **sin cambiar nada** y los selecciona | Para ver qué está mal en un modelo antes de tocarlo |

### 4.2. Retopo

Lo principal está siempre a la vista, en *Ajustes principales*:

| Opción | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Motor** | Quién hace el retopo | Déjalo en **Maya (polyRetopo, recomendado)**, el de por defecto. Los demás son para comparar o para casos concretos (abajo) |
| **Caras objetivo** | Cuántas caras tendrá el resultado (aproximado) | Un personaje para juego: 5.000–10.000. Para VFX o primeros planos: 15.000–30.000 |
| **Recomendar caras** | Mide el modelo y te dice el mínimo para no perder forma | Pulsas y dice «mínimo 9.000»: si pones 4.000, los dedos saldrán aplastados |
| **Resolución textura** | Tamaño de la textura de las UVs | 4096 va bien para casi todo |

Y estas están en **Avanzado**, apartado *Retopo*:

| Opción | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Simetría** | Hace una mitad y la refleja (por defecto, *No*) | Un personaje simétrico que vas a riggear: *Automática*, para que los dos lados tengan la misma topología |
| **Calidad** (Motor MBL) | Cuántas pruebas hace antes de quedarse con la mejor | *Rápida* para ver cómo queda; *Normal* para el resultado; *Alta* solo si tienes tiempo |
| **Tiempo máx. Motor MBL** | Tope de tiempo del Motor MBL | Si se alarga demasiado, bájalo: se queda con la mejor prueba terminada |

*Calidad* y *Tiempo máx. Motor MBL* solo se ven con *Automático* o *Motor MBL*.

**Los motores, en una línea cada uno:**
- **Maya (polyRetopo)** (recomendado, por defecto): solo quads, pocos polos, rápido y ordenado. En las pruebas con modelos reales rinde mejor que el Motor MBL. **Es el que sigue las guías.**
- **Automático:** hace Maya y el Motor MBL y se queda el de mejor nota. Tarda lo que los dos juntos, y como Maya suele ganar, normalmente basta con elegir Maya.
- **Motor MBL** (experimental): el nuestro. Solo quads, pero en las pruebas con modelos reales sus quads salen más deformados y desiguales que los de Maya, y aún no admite guías ni pincel. Está para probarlo o compararlo, no para trabajar.
- **Instant Meshes:** rápido. **Es el que usa el pincel de detalle.**

Si hay guías o detalle pintado, el plugin cambia de motor solo y lo dice en el log: con guías, Maya; con detalle pintado, Instant Meshes.

### 4.3. Más opciones de retopo (normalmente no hace falta tocarlas)

También están en **Avanzado**, apartado *Retopo*.

| Opción | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Ángulo aristas duras** | Respeta aristas marcadas a partir de ese ángulo. Lo usa solo **Instant Meshes**; con Maya, el Motor MBL y *Automático* solo cambia el sombreado (aristas duras o suaves) | Un casco o un arma con cantos vivos: elige el motor Instant Meshes y prueba 30–45°. En orgánicos, 0 (por defecto) |
| **Reproyectar sobre el original** | Pega la malla nueva a la forma original | Déjalo marcado: evita que el modelo «adelgace» |
| **Limpiar polos** | Quita parejas de polos girando aristas | Déjalo marcado: menos vértices raros |
| **Relajar quads** | Iguala el tamaño de los cuadrados | Si ves quads muy desiguales, súbelo un poco |
| **Ignorar piezas menores de** | Descarta restos flotantes pequeños | Un modelo de IA con «migas» sueltas alrededor: las ignora |
| **Rellenar agujeros de hasta** | Cierra agujeros pequeños del resultado | Agujeros diminutos que no estaban en el original |
| **Tiempo máximo** | Tope por malla | Para no quedarte esperando sin fin |

> Al elegir el motor **Instant Meshes** aparecen, dentro de *Avanzado*, sus opciones propias (suavizado, solo quads, determinista, alinear a los bordes, densidad por curvatura) y el recuadro *Instant Meshes* con la ruta del programa (si no lo encuentra solo, *Avanzado* se abre para que la indiques). Con los demás motores no se ven: no hace falta tocarlas.

### 4.4. Diagnóstico

Al final del apartado *Retopo*, en **Avanzado**.

| Botón | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Diagnóstico** | Cifras del retopo (caras, % quads, polos, error) y **pinta los polos**: azul = 3 aristas, rojo = 5, magenta = 6 o más | Para ver si hay polos en la cara antes de riggear |
| **Quitar colores** | Quita esos colores | **Hazlo siempre antes de exportar** a Substance |

### 4.5. UVs

Están en **Avanzado**, apartado *UVs* (la **Resolución textura** está a la vista, en *Ajustes principales*).

| Opción | Para qué sirve | Ejemplo rápido |
|---|---|---|
| **Respetar costuras existentes** | Usa tus cortes en vez de crear otros | Ya marcaste a mano los cortes del torso: los respeta |
| **Ángulo de costura** | Corta por aristas más afiladas que ese ángulo | Objetos duros con cantos: 60–90°. Orgánicos: 180 (no cortar por ángulo) |
| **Costuras automáticas de Unfold3D** | Añade los cortes mínimos para abrir sin solapes | Déjalo marcado |
| **Cortar tubos** | Corta brazos, dedos, piernas a lo largo | Personajes: marcado. Una roca: da igual |
| **Dividir islas** | 0 = el mínimo de islas; más alto = más islas y menos distorsión. Ya viene en **0,75**, el valor con el que más textura se aprovecha (por encima no cambia nada) | Déjalo así; bájalo solo si quieres menos cortes |
| **Costuras** | *Máxima resolución* aprovecha más la textura; *Costuras escondidas* las lleva a la espalda y a huecos como las axilas, con menos islas y algo menos de textura aprovechada. El modelo debe mirar a +Z | Un personaje que se verá de frente: *Costuras escondidas* |
| **UVs simétricas** | Isla izquierda y derecha idénticas | Pintas un brazo y puedes copiarlo al otro |
| **Costuras de sastre** | Cortes rectos y lógicos, como un patrón de ropa | Torso cortado por la espalda, nada por delante |
| **Islas rectas y de pie** | Ninguna isla torcida ni boca abajo | Que el «arriba» del modelo sea el arriba de la textura |
| **Optimize** | Reduce la distorsión | Más alto = un poco mejor y más lento |
| **Resolución textura** / **Padding** | Tamaño de textura y separación entre islas | 4096 y 16 px van bien para casi todo |
| **Permitir rotar islas** | Deja tumbar islas 90° para aprovechar más | Desmarcado (recomendado): las vetas y rayones siguen la misma dirección a los dos lados de un corte |
| **Calidad de layout** / **Intentos de empaquetado** | Precisión al colocar las islas | Más = más textura aprovechada, más tiempo |
| **UDIM**, **Nº de UDIMs**, **Densidad de texel** | Repartir en varias texturas. Con *Nº de UDIMs* en 0 usa la densidad de texel que pongas; con un número, la calcula el plugin | VFX con texturas 4K por zona: UDIM con 4–10 tiles |
| **Máx. caras para UVs** | No hace UVs en mallas con más caras que esto (150.000 por defecto; 0 = sin límite) | Protege la memoria: haz siempre el retopo antes |

### 4.6. Los botones de abajo y el semáforo

- **Limpiar**, **Retopo**, **UVs**: cada paso por separado. Ejemplo: tu malla ya tiene buena topología y solo quieres UVs → pulsa **UVs**.
- **Todo**: los tres pasos seguidos.
- **Cancelar**: para el proceso. Maya sigue vivo.
- **Comprobar estado**: calcula el semáforo de lo seleccionado sin cambiar nada.
- **Semáforo**: 🟢 listo · 🟡 revisar · 🔴 arreglar. Debajo de cada aviso, **→ qué tocar**. Doble clic en una malla la selecciona. En las UVs, además de solapes, espejos, estiramiento y textura aprovechada, mira la densidad de texel entre islas, el margen entre ellas y si los cortes casan, y nunca sale en verde si el juez de UVs las suspende.
- **Log**: qué hace y a qué hora (y el tiempo total al terminar). Si algo falla, el motivo está ahí (y en el Script Editor). **Limpiar log** borra el texto.

---

## 5. Guías de flujo, paso a paso

### Qué son

Trazos que dibujas **sobre el modelo** para decirle al retopo **por dónde quieres que pasen los loops**. El plugin convierte cada trazo en un camino de aristas del modelo y se lo da al motor de Maya como un rasgo que debe respetar.

> **Ejemplo:** sin guía, el retopo pone loops alrededor del ojo «a su manera». Con un anillo dibujado alrededor del ojo, lo normal es que salga un loop entero siguiendo tu trazo, con un número de caras parecido.

### Cuándo usarlas

- **Animación y VFX:** anillos en codos, rodillas, muñecas, hombros, cuello, y alrededor de **ojos** y **boca**. Son las zonas que se doblan.
- **Ropa y objetos:** a lo largo de una costura de ropa, del borde de una armadura o de una línea de diseño.
- **Texturizado:** cuando quieres un corte limpio de UV por un sitio concreto.

### Cómo se hace

1. Selecciona el **modelo original**.
2. Abre el grupo **Avanzado**, bloque **Guías de flujo**, y pulsa **Dibujar guías**.
   - El modelo se vuelve «vivo» (*live*): lo que dibujes se pega a su superficie.
   - Se activa el lápiz de Maya.
3. **Dibuja** arrastrando con el ratón en el visor.
   - **Un trazo = un loop.** Para un anillo, termina el trazo **justo encima de donde empezó**; así el camino se cierra. Si prefieres una curva cerrada de verdad (un círculo de Maya), ponla sobre el modelo, selecciona el modelo y la curva y pulsa **Usar curvas seleccionadas**.
   - **Cruzando el hueso:** en codos, rodillas y muñecas el anillo rodea el brazo o la pierna como una pulsera.
   - Puedes girar la cámara entre trazos.
4. Pulsa **Terminar guías** (el mismo botón). Los trazos quedan **amarillos**, en el grupo `<malla>_mblGuides`.
5. Pulsa **Retopo** o **Todo**.
6. **Comprueba** que los loops de la malla nueva siguen los trazos.

**Otros botones:**
- **Usar curvas seleccionadas:** si ya tienes curvas (dibujadas con otras herramientas), selecciona el modelo y las curvas, y pulsa.
- **Borrar guías:** quita todas las guías del modelo.

### Consejos para que salgan bien

- **Trazos suaves y limpios.** Un trazo en zigzag da loops en zigzag (y muchas más caras).
- **Cruces limpios.** Si dos guías se cruzan, mejor en ángulo recto: es lo habitual en topología (regla práctica, no medida en el plugin).
- **No las pongas demasiado juntas.** Dos guías paralelas deben estar separadas por unas cuantas caras (regla práctica).
- **Varias en una articulación:** para 3 loops en el codo, dibuja 3 anillos paralelos bien separados.
- **Menos es más:** empieza por 4–6 guías importantes, mira el resultado y añade si hace falta.

### Lo que hay que saber

- **Con guías se usa el motor de Maya** (el que mejor las sigue y el de por defecto). *Automático* y *Motor MBL* pasan a Maya solos; si eliges *Instant Meshes*, las sigue con su propio campo de direcciones, menos exacto. El Motor MBL todavía no admite guías.
- **Con guías se hace el modelo entero**, sin reflejar la mitad.
- **Red de seguridad:** si con las guías salen más de 1,6 veces las caras pedidas, el plugin repite el retopo **sin guías** y lo avisa en el log. Si te pasa, revisa si algún trazo está en zigzag o demasiado pegado a otro.
- **Las guías ponen loops donde quieres, pero no deciden dónde van los polos.** Revisa con **Diagnóstico** (polos de colores) antes de hacer el skin.

---

## 6. Pincel de detalle, paso a paso

### Qué es

Pintas en **rojo** las zonas que necesitan **más caras**. Lo pintado recibe más caras por unidad de área y lo no pintado conserva su densidad: el plugin suma al total las caras extra y lo dice en el log («detalle pintado en el X % de la superficie: N caras (M extra para el detalle)»).

> **Ejemplo:** humano a 5.000 caras con la cabeza pintada (×4). La cabeza recibe 1.732 caras, frente a 828 con Maya y 356 con Instant Meshes sin pintar, y el total sube a 7.392.

### Cuándo usarlo

- **Cara y manos** de un personaje, que necesitan más detalle que la espalda.
- **Piezas finas** que se aplastan: dedos, orejas, garras, plumas. Si el log dice que mucha superficie queda en piezas más finas que 3 aristas, te propondrá pintar.
- **Zonas que verás de cerca** en un plano de VFX.

### Cómo se hace

1. Selecciona el **modelo original**.
2. Pulsa **Pintar detalle** (en **Avanzado**, bloque *Pincel de detalle*). Se abre el pincel de color de vértices de Maya, ya en rojo.
3. **Pinta** sobre las zonas que quieras con más detalle.
   - **Más rojo = más detalle.** Lo negro (sin pintar) queda normal.
   - Cambia el tamaño del pincel con **B + arrastrar**, el atajo de Maya para sus pinceles (o con *Radius* en las opciones del pincel). Es la herramienta *Paint Vertex Color* de Maya; el plugin no cambia sus atajos.
   - Para borrar una zona, en las opciones del pincel (doble clic en el icono de la herramienta) pon el color en **negro** y pinta encima: el plugin deja el pincel en modo «reemplazar» y el negro cuenta como sin detalle. Al volver a pulsar **Pintar detalle**, el color vuelve a ser rojo.
4. Ajusta **Densidad en lo pintado** (por defecto **4x** = cuatro veces más caras por área en lo rojo; de 1,5x a 16x).
5. Pulsa **Retopo** o **Todo**.

**Borrar detalle** quita todo lo pintado.

### Consejos

- **Pinta solo lo necesario.** El pincel usa el motor **Instant Meshes**, que sigue la forma peor que Maya (en el humano: error medio 0,18 % frente a 0,07 %). Cuanto menos pintes, mejor.
- **No pintes todo el modelo:** sería lo mismo que subir *Caras objetivo*, pero peor.
- **Transiciones suaves:** no hace falta pintar con bordes duros; el tamaño de las caras cambia de forma gradual entre lo pintado y lo no pintado.
- **Valores altos (8x–16x)** solo en zonas pequeñas. Con densidades muy grandes, las piezas finas pueden romperse.

### Lo que hay que saber

- Con zonas pintadas **se usa Instant Meshes**, el único que admite densidad variable. El Motor MBL todavía no admite el pincel.
- **Guías + detalle a la vez:** se usa Instant Meshes con las dos cosas, pero las guías se siguen **menos exactas** que con Maya.
- El rojo que pintas se guarda en un juego de colores propio (`MBL_detalle`): no pisa los colores que ya tuviera el modelo.

---

## 7. Recetas rápidas

### Personaje para animar o VFX
1. *Simetría* (en **Avanzado**): **Automática** (si es simétrico).
2. **Recomendar caras** y súbelo un poco (cara y manos lo agradecen).
3. **Guías**: anillos en hombros, codos, muñecas, cadera, rodillas, tobillos, cuello, ojos y boca.
4. **Todo**.
5. **Diagnóstico**: que no haya polos (rojo/magenta) dentro de las articulaciones ni en las comisuras. **Quitar colores**.

### Criatura de IA para Substance Painter
1. **Recomendar caras** y pon esa cifra.
2. Motor **Maya** (el de por defecto).
3. *Costuras*: **Costuras escondidas** si se verá de frente.
4. **Todo** → semáforo en verde → exporta el `_retopo`.

### Prop u objeto duro (arma, casco, mueble)
1. Motor **Instant Meshes** (es el único que respeta el *Ángulo aristas duras*) con 30–45°.
2. *Ángulo de costura* (UVs): 60–90°.
3. **Todo**. Si los cantos siguen saliendo redondeados, sube las caras.

### Personaje con dedos juntos o ropa con pliegues
1. **Recomendar caras**: si avisa de piezas finas, **Pintar detalle** en manos y pliegues.
2. Si salen agujeros o caras dobladas entre los dedos, sube las caras y comprueba que el motor es **Maya** (el de por defecto).

---

## 8. Problemas frecuentes

| Problema | Qué hacer |
|---|---|
| El semáforo sale en rojo | Lee la línea **→** debajo del aviso: dice exactamente qué tocar |
| Tarda mucho | Menos caras, o motor **Maya** (con el Motor MBL o *Automático*, también Calidad **Rápida**) |
| El PC va lento mientras calcula | Normal con muchas caras. El plugin trabaja con prioridad baja y se para solo si la memoria va justa |
| Dice que no caben tantas caras | Te dice cuántas puedes pedir: pon esa cifra o usa el motor **Maya** |
| Las guías no se respetan | Mira el log: si salieron demasiadas caras, se repitió sin guías. Revisa trazos en zigzag o muy juntos |
| Los dedos salen aplastados o pegados | Más caras (mira *Recomendar caras*) o **Pintar detalle** en las manos |
| Error *non-manifold* en las UVs | Comprueba que **Reparar no-manifold** está marcado (viene marcado) y pulsa **Limpiar** |
| No me gusta el resultado | **Ctrl+Z**, cambia el motor, las caras o la simetría, y repite |
