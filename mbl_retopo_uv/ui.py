# -*- coding: utf-8 -*-
"""Ventana PySide6 de MBL Retopo UV, hija de la ventana principal de Maya."""

import html
import os
import time

import maya.cmds as cmds
from PySide6 import QtCore, QtGui, QtWidgets

from . import __version__, cleanup, config, detail, diagnostics, guides, pipeline, retopo, status
from .common import MBLError, format_duration, selected_mesh_transforms, short_name, stamp

WINDOW_OBJECT_NAME = "MBLRetopoUVWindow"
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "mbl.png")
LOG_COLORS = {"info": None, "warning": "#e0b050", "error": "#ff6b6b"}


def maya_main_window():
    """Ventana principal de Maya como QWidget (None en mayapy sin interfaz)."""
    from maya import OpenMayaUI as omui
    from shiboken6 import wrapInstance
    pointer = omui.MQtUtil.mainWindow()
    return wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None


def _spin(minimum, maximum, value, step=1, decimals=None, suffix="", tip=""):
    if decimals is None:
        widget = QtWidgets.QSpinBox()
    else:
        widget = QtWidgets.QDoubleSpinBox()
        widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setSingleStep(step)
    widget.setValue(value)
    if suffix:
        widget.setSuffix(suffix)
    widget.setToolTip(tip)
    return widget


def _check(text, tip=""):
    widget = QtWidgets.QCheckBox(text)
    widget.setToolTip(tip)
    return widget


STYLE = """
QGroupBox { font-weight: bold; margin-top: 12px; padding-top: 8px; border: 1px solid #5a5a5a; border-radius: 5px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QComboBox, QGroupBox QSpinBox, QGroupBox QDoubleSpinBox { font-weight: normal; }
QLabel#note { color: #8a94a6; font-weight: normal; padding: 2px 0 6px 0; }
QPushButton { padding: 5px 10px; border: 1px solid #7a7a7a; border-radius: 4px; background-color: rgba(128, 128, 128, 45); }
QPushButton:hover { background-color: rgba(128, 128, 128, 90); }
QPushButton:disabled { color: #8a8a8a; }
QPushButton#primary { background-color: #2f6fed; color: white; font-weight: bold; padding: 7px 10px; }
QPushButton#primary:hover { background-color: #4a85ff; }
QPushButton#primary:disabled { background-color: #4a4a4a; color: #8a8a8a; }
QProgressBar { border: 1px solid #5a5a5a; border-radius: 4px; text-align: center; height: 16px; }
QProgressBar::chunk { background-color: #2f6fed; border-radius: 3px; }
"""

ENGINE_NOTES = {
    "maya": "polyRetopo de Maya. Recomendado: solo quads, pocos polos y el más rápido de los buenos; en las pruebas con modelos reales rinde mejor que el Motor MBL.",
    "auto": "Hace el retopo con Maya y con el Motor MBL (experimental) y se queda el de mejor nota. Tarda lo que los dos juntos (de 30 s a 2 min); como Maya suele ganar, normalmente basta con elegir Maya.",
    "mbl": "EXPERIMENTAL, no recomendado para trabajar. Nuestro motor: solo quads y simétrico con bucle central, pero en las pruebas con modelos reales sus quads salen más deformados y desiguales que los de Maya. Sin guías ni detalle pintado. De 30 s a 2 min. Úsalo para probar o comparar.",
    "instant_meshes": "Rápido, con opciones propias (en Avanzado). Menos preciso que los demás.",
    "nativo": "EN PRUEBAS. El motor nuevo, desde cero: loops que siguen la forma, polos donde los pondría un artista y más caras donde la superficie se curva. Aún puede dejar algún agujero pequeño. Sin guías ni detalle pintado.",
}


class RetopoUVWindow(QtWidgets.QDialog):

    def __init__(self, parent=None, settings_file=None):
        super(RetopoUVWindow, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("MBL Retopo UV  {}".format(__version__))
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QtGui.QIcon(ICON_PATH))
        self.resize(540, 880)
        self._settings_file = settings_file
        self._cancel_requested = False
        self._running = False
        self._build()
        self.apply_settings(config.load(self._settings_file))

    # ------------------------------------------------------------------ construcción

    def _build(self):
        w = {}
        self.widgets = w

        # Instant Meshes
        w["instant_meshes_path"] = QtWidgets.QLineEdit()
        w["instant_meshes_path"].setPlaceholderText("Ruta de Instant Meshes (.exe o .app)")
        browse = QtWidgets.QPushButton("Buscar...")
        browse.clicked.connect(self._browse_executable)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(w["instant_meshes_path"])
        path_row.addWidget(browse)
        link = QtWidgets.QLabel('<a href="{0}">Descargar Instant Meshes (gratis)</a>'.format(config.DOWNLOAD_URL))
        link.setOpenExternalLinks(True)
        im_box = QtWidgets.QGroupBox("Instant Meshes")
        im_layout = QtWidgets.QVBoxLayout(im_box)
        im_layout.addLayout(path_row)
        im_layout.addWidget(link)

        # Limpieza
        w["cleanup_delete_history"] = _check("Borrar historial")
        w["cleanup_freeze"] = _check("Congelar transformaciones (freeze)")
        w["cleanup_merge"] = _check("Fusionar vértices")
        w["cleanup_merge_distance"] = _spin(0.0, 10.0, 0.001, 0.0005, 5,
                                            tip="Distancia máxima, en unidades de escena, para fusionar vértices.")
        w["cleanup_conform_normals"] = _check("Conformar normales",
                                              "Hace que todas las caras miren hacia el mismo lado.")
        w["cleanup_fix_nonmanifold"] = _check("Reparar no-manifold",
                                              "Separa aristas y vértices no-manifold (Unfold3D no admite esa geometría).")
        cleanup_box = QtWidgets.QGroupBox("Limpieza")
        form = QtWidgets.QFormLayout(cleanup_box)
        form.addRow(w["cleanup_delete_history"])
        form.addRow(w["cleanup_freeze"])
        form.addRow(w["cleanup_merge"], w["cleanup_merge_distance"])
        form.addRow(w["cleanup_conform_normals"])
        form.addRow(w["cleanup_fix_nonmanifold"])
        analyze = QtWidgets.QPushButton("Analizar y seleccionar problemas")
        analyze.setToolTip("Busca no-manifold, caras de área cero y vértices sueltos sin cambiar nada.")
        analyze.clicked.connect(self._analyze)
        form.addRow(analyze)

        # Retopo
        w["retopo_engine"] = QtWidgets.QComboBox()
        for key, label in (("maya", "Maya (polyRetopo, recomendado)"), ("auto", "Automático (el mejor de Maya y Motor MBL)"),
                           ("mbl", "Motor MBL (experimental)"), ("instant_meshes", "Instant Meshes"),
                           ("nativo", "Motor nativo (en pruebas)")):
            w["retopo_engine"].addItem(label, key)
        w["retopo_engine"].setToolTip(
            "Maya (polyRetopo): recomendado. En las pruebas, 100% quads, muchos menos polos y menos error que Instant "
            "Meshes, y mejor calidad de malla que el Motor MBL. El más rápido de los buenos.\n"
            "Automático: hace el retopo con Maya y con el Motor MBL, y se queda el de mejor forma, menos polos y "
            "solo quads. Tarda lo que los dos juntos (de 30 s a 2 min).\n"
            "Motor MBL (experimental): nuestro motor. QuadriFlow con las piezas finas engordadas, ajuste final a la forma y la "
            "mejor de 3 variantes. Solo quads y sin agujeros, pero en las pruebas con modelos reales sus quads salen más "
            "deformados y desiguales que los de Maya, así que no es el recomendado. Hace siempre el modelo entero (sin "
            "reflejar la mitad) y no admite guías ni detalle pintado.\n"
            "Instant Meshes: más rápido; las opciones de quads, determinista, suavizado, ángulo y densidad "
            "por curvatura solo afectan a este motor.")
        w["retopo_target_faces"] = _spin(50, 2000000, 5000, 500, tip="Número aproximado de caras del resultado.")
        w["retopo_symmetry"] = QtWidgets.QComboBox()
        for key, label in (("off", "No"), ("auto", "Automática (si el modelo es simétrico en X)"),
                           ("x", "Siempre en X")):
            w["retopo_symmetry"].addItem(label, key)
        w["retopo_symmetry"].setToolTip(
            "Hace el retopo de la mitad +X y la refleja: los dos lados quedan con la misma topología "
            "(polos y loops en el mismo sitio), como se espera de un personaje.\n"
            "Automática: lo detecta comparando el modelo con su reflejo. El plano es el centro del modelo en X; "
            "si tu modelo es simétrico en otro eje, gíralo antes.\n"
            "Con el Motor MBL, si la simetría deja más fallos en la costura que el retopo sin ella, el resultado "
            "sale sin simetría y se avisa en la ventana.")
        w["retopo_crease_angle"] = _spin(0.0, 180.0, 0.0, 5.0, 1, "°",
                                         "Ángulo a partir del cual una arista se considera dura y se respeta. 0 = desactivado.")
        w["retopo_smooth"] = _spin(0, 20, 2, tip="Iteraciones de suavizado y reproyección de Instant Meshes.")
        w["retopo_pure_quads"] = _check("Solo quads (rosy 4 / posy 4)",
                                        "Desmarcado: malla mayoritariamente de quads con algún triángulo.")
        w["retopo_deterministic"] = _check("Determinista (mismo resultado siempre)")
        w["retopo_align_boundaries"] = _check("Alinear a los bordes abiertos")
        w["retopo_reproject"] = _check("Reproyectar sobre el original (no perder volumen)")
        w["retopo_adaptive"] = _spin(0.0, 1.0, 0.0, 0.1, 2,
                                     tip="Densidad por curvatura (experimental, versión MBL de Instant Meshes): "
                                         "caras más grandes en zonas planas y más pequeñas en curvas. 0 = uniforme. "
                                         "Con 0.4 suele bajar el error de forma un 10-25%, pero añade polos.")
        w["retopo_adaptive_ratio"] = _spin(1.0, 10.0, 3.0, 0.5, 1,
                                           tip="Cuánto más grande puede ser una cara respecto a la media.")
        w["retopo_min_piece"] = _spin(0.0, 50.0, 1.0, 0.5, 2, " %",
                                      "Piezas sueltas con menos de este % de caras se ignoran (restos flotantes). 0 = usar todo.")
        w["retopo_fill_holes"] = _spin(0, 10000, 60, 10, suffix=" aristas",
                                       tip="Rellena los agujeros del resultado con este nº de aristas o menos. 0 = no rellenar.")
        w["retopo_pole_cleanup"] = _check("Limpiar polos",
                                          "Quita parejas de polos (vértices de 3 y 5 aristas) girando aristas y "
                                          "cerrando quads, como se haría a mano.")
        w["retopo_relax"] = _spin(0, 100, 10, tip="Pasadas de relajación: iguala el tamaño de los quads deslizándolos "
                                                  "por la superficie del original. 0 = no relajar.")
        w["retopo_timeout"] = _spin(10, 36000, 600, 60, suffix=" s", tip="Tiempo máximo por malla.")
        w["pipeline_reuse_duplicates"] = _check("Piezas repetidas: hacerlas una sola vez",
                                                "Si seleccionas varias mallas idénticas (mismos vértices y caras, misma forma y "
                                                "tamaño salvo giro y movimiento, como 20 tornillos copiados), el retopo y las UVs "
                                                "se hacen de una y el resultado se copia a las demás, cada una en su sitio, con su "
                                                "grupo y sus materiales. Solo con el paso Retopo marcado.")
        retopo_box = QtWidgets.QGroupBox("Retopo")
        form = QtWidgets.QFormLayout(retopo_box)
        w["mbl_variants"] = QtWidgets.QComboBox()
        for value, label in ((1, "Rápida (1 prueba)"), (3, "Normal (la mejor de 3)"), (6, "Alta (la mejor de 6, el doble de tiempo)")):
            w["mbl_variants"].addItem(label, value)
        w["mbl_variants"].setToolTip("El Motor MBL prueba varios engordados de las piezas finas y se queda con el de "
                                     "mejor forma y menos polos. Más pruebas = algo mejor y más lento.")
        w["mbl_time_budget"] = _spin(30, 3600, 270, 30, suffix=" s",
                                     tip="Tiempo total aproximado del Motor MBL (preparación incluida). No empieza una prueba que, por lo que han "
                                         "tardado las anteriores, no vaya a terminar dentro de este tiempo, y se queda con la mejor "
                                         "de las que terminaron.")
        self.engine_note = QtWidgets.QLabel()
        self.engine_note.setWordWrap(True)
        self.engine_note.setObjectName("note")

        # lo principal, siempre a la vista: motor, caras objetivo y resolución de textura (se añade con las UVs);
        # el resto de opciones va plegado en «Avanzado»
        main_box = QtWidgets.QGroupBox("Ajustes principales")
        main_form = QtWidgets.QFormLayout(main_box)
        main_form.addRow("Motor", w["retopo_engine"])
        main_form.addRow(self.engine_note)
        main_form.addRow("Caras objetivo", w["retopo_target_faces"])
        recommend = QtWidgets.QPushButton("Recomendar caras")
        recommend.setToolTip("Mide la malla seleccionada y dice cuántas caras hacen falta para no perder la forma "
                             "(mínimo recomendado) y cuántas para conservar también las piezas más finas (máximo detalle).")
        recommend.clicked.connect(self._recommend_faces)
        self.faces_note = QtWidgets.QLabel()
        self.faces_note.setWordWrap(True)
        self.faces_note.setObjectName("note")
        main_form.addRow(recommend)
        main_form.addRow(self.faces_note)

        form.addRow("Simetría", w["retopo_symmetry"])

        # opciones del Motor MBL (solo se ven con Motor MBL o Automático)
        self.mbl_options = QtWidgets.QWidget()
        mbl_form = QtWidgets.QFormLayout(self.mbl_options)
        mbl_form.setContentsMargins(0, 0, 0, 0)
        mbl_form.addRow("Calidad", w["mbl_variants"])
        mbl_form.addRow("Tiempo máx. Motor MBL", w["mbl_time_budget"])
        form.addRow(self.mbl_options)

        # opciones de Instant Meshes (solo se ven con Instant Meshes)
        self.im_options = QtWidgets.QWidget()
        im_form = QtWidgets.QFormLayout(self.im_options)
        im_form.setContentsMargins(0, 0, 0, 0)
        im_form.addRow("Suavizado", w["retopo_smooth"])
        im_form.addRow(w["retopo_pure_quads"])
        im_form.addRow(w["retopo_deterministic"])
        im_form.addRow(w["retopo_align_boundaries"])
        im_form.addRow("Densidad por curvatura", w["retopo_adaptive"])
        im_form.addRow("Relación máx. de tamaño", w["retopo_adaptive_ratio"])
        form.addRow(self.im_options)

        form.addRow("Ángulo aristas duras", w["retopo_crease_angle"])
        form.addRow(w["retopo_reproject"])
        form.addRow(w["retopo_pole_cleanup"])
        form.addRow("Relajar quads", w["retopo_relax"])
        form.addRow("Ignorar piezas menores de", w["retopo_min_piece"])
        form.addRow("Rellenar agujeros de hasta", w["retopo_fill_holes"])
        form.addRow("Tiempo máximo", w["retopo_timeout"])
        form.addRow(w["pipeline_reuse_duplicates"])
        w["retopo_engine"].currentIndexChanged.connect(self._update_engine_ui)
        # guías y pincel de detalle
        w["retopo_detail_density"] = _spin(1.5, 16.0, 4.0, 0.5, 1, "x",
                                           "Cuántas veces más caras por unidad de área tendrán las zonas "
                                           "pintadas. Las caras extra se suman al total, así que lo no pintado no pierde detalle.")
        self.guide_button = QtWidgets.QPushButton("Dibujar guías")
        self.guide_button.setToolTip(
            "Selecciona el modelo original y pulsa: dibuja trazos sobre él (alrededor de ojos, boca, "
            "costuras de ropa...) y pulsa 'Terminar guías'. El retopo pondrá loops siguiendo los trazos.")
        self.guide_button.clicked.connect(self._toggle_guides)
        use_curves = QtWidgets.QPushButton("Usar curvas seleccionadas")
        use_curves.setToolTip("Selecciona el modelo y una o varias curvas tuyas para usarlas como guías.")
        use_curves.clicked.connect(self._use_selected_curves)
        clear_guides = QtWidgets.QPushButton("Borrar guías")
        clear_guides.clicked.connect(self._clear_guides)
        paint_detail = QtWidgets.QPushButton("Pintar detalle")
        paint_detail.setToolTip("Selecciona el modelo original y pinta en rojo las zonas que necesitan más "
                                "caras (cara, manos...). Más rojo = más detalle.")
        paint_detail.clicked.connect(self._paint_detail)
        clear_detail = QtWidgets.QPushButton("Borrar detalle")
        clear_detail.clicked.connect(self._clear_detail)
        guide_row = QtWidgets.QHBoxLayout()
        for button in (self.guide_button, use_curves, clear_guides):
            guide_row.addWidget(button)
        detail_row = QtWidgets.QHBoxLayout()
        detail_row.addWidget(paint_detail)
        detail_row.addWidget(clear_detail)
        guides_box = QtWidgets.QGroupBox("Guías de flujo (motor de Maya)")
        QtWidgets.QVBoxLayout(guides_box).addLayout(guide_row)
        detail_box = QtWidgets.QGroupBox("Pincel de detalle (usa Instant Meshes)")
        detail_form = QtWidgets.QFormLayout(detail_box)
        detail_form.addRow(detail_row)
        detail_form.addRow("Densidad en lo pintado", w["retopo_detail_density"])
        self._guide_session = None

        diagnose_button = QtWidgets.QPushButton("Diagnóstico")
        diagnose_button.setToolTip("Métricas del retopo seleccionado (caras, % quads, polos, error frente al original) "
                                   "y polos coloreados: azul 3 aristas, rojo 5, magenta 6 o más.")
        diagnose_button.clicked.connect(self._diagnose)
        clear_colors = QtWidgets.QPushButton("Quitar colores")
        clear_colors.setToolTip("Quita los colores de polos. Hazlo antes de exportar a Substance Painter.")
        clear_colors.clicked.connect(self._clear_pole_colors)
        diagnose_row = QtWidgets.QHBoxLayout()
        diagnose_row.addWidget(diagnose_button)
        diagnose_row.addWidget(clear_colors)
        form.addRow(diagnose_row)

        # UVs
        w["uv_respect_existing"] = _check("Respetar costuras existentes",
                                          "Si la malla ya tiene costuras UV, se usan esas y no se añaden automáticas.")
        w["uv_seam_angle"] = _spin(1.0, 180.0, 180.0, 5.0, 1, "°",
                                   "Se corta por las aristas cuyo ángulo supera este valor. 180 = no cortar por ángulo.")
        w["uv_material_seams"] = _check("Bordes de material como costuras",
                                        "Si la malla tiene varios materiales, las aristas entre caras de materiales distintos "
                                        "son costura: cada material tiene sus propias islas.")
        w["uv_auto_seams"] = _check("Costuras automáticas de Unfold3D",
                                    "Añade los cortes mínimos para poder desplegar sin solapes.")
        w["uv_cut_pipes"] = _check("Cortar tubos (brazos, dedos...)")
        w["uv_split_shells"] = _spin(0.0, 1.0, 0.0, 0.05, 2,
                                     tip="0 = el mínimo de islas. Valores altos = más islas, menos distorsión.")
        w["uv_seam_style"] = QtWidgets.QComboBox()
        w["uv_seam_style"].addItem("Máxima resolución", "resolution")
        w["uv_seam_style"].addItem("Costuras escondidas", "hidden")
        w["uv_seam_style"].setToolTip(
            "Máxima resolución: islas por partes, aprovechan al máximo la textura.\n"
            "Costuras escondidas: menos islas y costuras detrás y en huecos (axilas, entrepierna); "
            "el modelo debe mirar a +Z. Aprovecha algo menos la textura.")
        w["uv_symmetric"] = _check("UVs simétricas",
                                   "Si el modelo es simétrico en X, cada isla de un lado tiene su gemela "
                                   "idéntica en el otro (brazo izquierdo y derecho).")
        w["uv_tailor_seams"] = _check("Costuras de sastre",
                                      "Cortes rectos y por sitios lógicos: el corte de cada tubo (torso, "
                                      "brazos, piernas) va a la espalda o bajo los brazos, nada por delante "
                                      "del pecho, y sin islas diminutas.")
        w["uv_straight_islands"] = _check("Islas rectas y de pie",
                                          "Cada isla con el arriba del modelo hacia arriba (o tumbada 90°), "
                                          "nunca torcida ni boca abajo.")
        w["uv_straighten_strips"] = _check("Enderezar tubos (brazos, piernas...)",
                                           "Las islas que son tubos cortados a lo largo (brazos, piernas, dedos, cables) "
                                           "se enderezan para que las dos orillas del corte sean paralelas y el dibujo de "
                                           "la textura (vetas, rayones) siga la misma dirección al cruzar la costura. Solo "
                                           "se toca una isla si no estira más que antes.")
        w["uv_fix_stretch"] = _check("Corregir islas estiradas",
                                     "Mide el estiramiento de cada isla tras desplegar y, si una estira de más, prueba a "
                                     "relajarla con Optimize más fuerte o a darle un corte extra. Solo se acepta si la isla "
                                     "mejora, sin solapes ni caras en espejo.")
        w["uv_optimize_iterations"] = _spin(0, 100, 5, tip="Iteraciones de Optimize (reduce distorsión).")
        w["uv_map_size"] = QtWidgets.QComboBox()
        for size in config.MAP_SIZES:
            w["uv_map_size"].addItem(str(size), size)
        w["uv_padding_px"] = _spin(0, 128, 0, 2, suffix=" px",
                                   tip="Separación entre islas a la resolución elegida. 0 = automática: 1/256 del lado "
                                       "de la textura (8 px a 2K, 16 a 4K, 32 a 8K), así las islas no se mezclan al "
                                       "bajar la textura a mipmaps. Un número fijo se respeta tal cual.")
        w["uv_allow_rotation"] = _check("Permitir rotar islas (pasos de 90°)",
                                        "Desmarcado (recomendado): todas las islas quedan de pie y el dibujo de la textura "
                                        "(vetas, rayones en proyección UV) sigue la misma dirección a los dos lados de un corte. "
                                        "Marcado: el empaquetador puede tumbarlas 90° y aprovechar algo más (o menos) la textura.")
        w["uv_layout_quality"] = QtWidgets.QComboBox()
        for quality in (128, 256, 512, 1024):
            w["uv_layout_quality"].addItem(str(quality), quality)
        w["uv_layout_quality"].setToolTip("Precisión del empaquetado. Más alto = mejor aprovechado pero más lento.")
        w["uv_pack_tries"] = _spin(1, 5, 3, tip="Combinaciones de empaquetado que se prueban para quedarse con la "
                                                "que más textura aprovecha. 1 = la más rápida; 3 suele ganar "
                                                "un 5-15% de resolución a cambio de unos segundos.")
        w["uv_udim"] = _check("UDIM (varios tiles)")
        w["uv_texel_density"] = _spin(0.01, 10000.0, 10.24, 1.0, 2, " px/u",
                                      "Densidad de texel en píxeles por unidad de escena (cm). Solo en modo UDIM.")
        w["uv_udim_tiles"] = _spin(0, 100, 4, tip="Cuántos UDIMs llenar: el plugin calcula la densidad de texel "
                                                  "para aprovecharlos al máximo. 0 = usar la densidad indicada.")
        w["uv_udim"].toggled.connect(w["uv_udim_tiles"].setEnabled)
        w["uv_udim"].toggled.connect(self._update_density_enabled)
        w["uv_udim_tiles"].valueChanged.connect(self._update_density_enabled)
        w["uv_common_texel"] = _check("Misma densidad de texel entre mallas",
                                      "Con varias mallas a la vez (personaje, ropa, accesorios) todas quedan con los mismos "
                                      "píxeles por unidad de escena: la densidad de la malla más grande, que es la que menos "
                                      "textura da por unidad, y las demás se escalan a ella y ocupan solo parte de su textura. "
                                      "Desmarcado: cada malla llena su textura por separado. Con una sola malla no cambia nada.")
        w["uv_max_faces"] = _spin(0, 10000000, 150000, 10000,
                                  tip="Si la malla tiene más caras, no se hacen UVs (podría agotar la memoria). 0 = sin límite.")
        uv_box = QtWidgets.QGroupBox("UVs")
        form = QtWidgets.QFormLayout(uv_box)
        form.addRow(w["uv_respect_existing"])
        form.addRow("Ángulo de costura", w["uv_seam_angle"])
        form.addRow(w["uv_auto_seams"])
        form.addRow(w["uv_material_seams"])
        form.addRow(w["uv_cut_pipes"])
        form.addRow("Dividir islas", w["uv_split_shells"])
        form.addRow("Costuras", w["uv_seam_style"])
        form.addRow(w["uv_symmetric"])
        form.addRow(w["uv_tailor_seams"])
        form.addRow(w["uv_straight_islands"])
        form.addRow(w["uv_straighten_strips"])
        form.addRow(w["uv_fix_stretch"])
        checker = QtWidgets.QPushButton("Ver cuadrícula")
        checker.setToolTip("Pone a la malla seleccionada una textura de casillas numeradas temporal para ver a ojo el "
                           "estiramiento y la orientación de las UVs (en el visor, tecla 6 para ver texturas). Un segundo "
                           "clic la quita y devuelve el material de antes. No cambia las UVs.")
        checker.clicked.connect(self._toggle_checker)
        form.addRow(checker)
        form.addRow("Optimize", w["uv_optimize_iterations"])
        form.addRow("Padding (0 = automático)", w["uv_padding_px"])
        form.addRow(w["uv_allow_rotation"])
        form.addRow("Calidad de layout", w["uv_layout_quality"])
        form.addRow("Intentos de empaquetado", w["uv_pack_tries"])
        form.addRow(w["uv_udim"])
        form.addRow("Nº de UDIMs", w["uv_udim_tiles"])
        form.addRow("Densidad de texel", w["uv_texel_density"])
        form.addRow(w["uv_common_texel"])
        form.addRow("Máx. caras para UVs", w["uv_max_faces"])
        main_form.addRow("Resolución textura", w["uv_map_size"])
        reset = QtWidgets.QPushButton("Valores por defecto")
        reset.setToolTip("Devuelve TODOS los ajustes de la ventana a los valores de fábrica. Solo se conserva la ruta de Instant Meshes.")
        reset.clicked.connect(self._reset_defaults)
        main_form.addRow(reset)

        # todas las demás opciones, plegadas
        self.advanced_box = QtWidgets.QGroupBox("Avanzado")
        self.advanced_box.setCheckable(True)
        self.advanced_box.setChecked(False)
        self.advanced_box.setToolTip("Limpieza, opciones de cada motor, simetría, guías de flujo, detalle pintado y UVs. "
                                     "Los valores por defecto valen para la mayoría de los modelos.")
        advanced_inner = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(advanced_inner)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.im_box = im_box
        for box in (cleanup_box, retopo_box, im_box, guides_box, detail_box, uv_box):
            advanced_layout.addWidget(box)
        advanced_outer = QtWidgets.QVBoxLayout(self.advanced_box)
        advanced_outer.addWidget(advanced_inner)
        advanced_inner.setVisible(False)
        self.advanced_box.toggled.connect(advanced_inner.setVisible)

        options = QtWidgets.QWidget()
        options_layout = QtWidgets.QVBoxLayout(options)
        options_layout.addWidget(main_box)
        options_layout.addWidget(self.advanced_box)
        options_layout.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(options)

        # botones
        self.run_buttons = []
        buttons = QtWidgets.QGridLayout()
        all_button = QtWidgets.QPushButton("Todo: limpiar → retopo → UVs")
        all_button.setObjectName("primary")
        all_button.clicked.connect(lambda: self.run_steps(["cleanup", "retopo", "uv"]))
        buttons.addWidget(all_button, 0, 0, 1, 2)
        self.run_buttons.append(all_button)
        self.cancel_button = QtWidgets.QPushButton("Cancelar")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._request_cancel)
        buttons.addWidget(self.cancel_button, 0, 2)
        for column, (text, steps) in enumerate([("Limpiar", ["cleanup"]), ("Retopo", ["retopo"]), ("UVs", ["uv"])]):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(lambda checked=False, s=steps: self.run_steps(s))
            buttons.addWidget(button, 1, column)
            self.run_buttons.append(button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.status = QtWidgets.QLabel("Selecciona una o varias mallas.")
        # semáforo
        self.status_tree = QtWidgets.QTreeWidget()
        self.status_tree.setHeaderHidden(True)
        self.status_tree.setWordWrap(True)
        self.status_tree.setMinimumHeight(110)
        self.status_tree.setToolTip("Estado de cada malla tras el último proceso. Verde: listo. Amarillo: se puede "
                                    "usar, pero conviene mirarlo. Rojo: hay que arreglarlo. Doble clic en una "
                                    "malla para seleccionarla.")
        self.status_tree.itemDoubleClicked.connect(self._select_status_mesh)
        check_status = QtWidgets.QPushButton("Comprobar estado")
        check_status.setToolTip("Calcula el semáforo de las mallas seleccionadas sin cambiar nada.")
        check_status.clicked.connect(self._check_status)
        status_header = QtWidgets.QHBoxLayout()
        status_header.addWidget(QtWidgets.QLabel("Estado"))
        status_header.addStretch()
        status_header.addWidget(check_status)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(140)
        clear_log = QtWidgets.QPushButton("Limpiar log")
        clear_log.clicked.connect(self.log_view.clear)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(scroll)
        log_panel = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addLayout(buttons)
        log_layout.addWidget(self.progress)
        log_layout.addWidget(self.status)
        log_layout.addLayout(status_header)
        log_layout.addWidget(self.status_tree, 1)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addWidget(clear_log, alignment=QtCore.Qt.AlignRight)
        splitter.addWidget(log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(splitter)
        self.setStyleSheet(STYLE)

    # ------------------------------------------------------------------ ajustes

    def apply_settings(self, settings):
        for key, widget in self.widgets.items():
            value = settings.get(key, config.DEFAULTS[key])
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QtWidgets.QLineEdit):
                widget.setText(value)
            elif isinstance(widget, QtWidgets.QComboBox):
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else widget.findData(config.DEFAULTS[key]))
            else:
                widget.setValue(value)
        self._update_density_enabled()
        self._update_engine_ui()

    def _update_engine_ui(self, *args):
        """Muestra solo las opciones del motor elegido y su descripción."""
        engine = self.widgets["retopo_engine"].currentData()
        self.engine_note.setText(ENGINE_NOTES.get(engine, ""))
        self.mbl_options.setVisible(engine in ("auto", "mbl"))
        self.im_options.setVisible(engine == "instant_meshes")
        self.im_box.setVisible(engine == "instant_meshes")
        if engine == "instant_meshes" and not config.find_instant_meshes(self.collect_settings()):
            # la ruta del programa está en «Avanzado»: si no se encuentra solo, que se vea dónde indicarla
            self.advanced_box.setChecked(True)

    def _update_density_enabled(self, *args):
        udim = self.widgets["uv_udim"].isChecked()
        self.widgets["uv_udim_tiles"].setEnabled(udim)
        self.widgets["uv_texel_density"].setEnabled(udim and self.widgets["uv_udim_tiles"].value() == 0)

    def collect_settings(self):
        settings = dict(config.DEFAULTS)
        for key, widget in self.widgets.items():
            if isinstance(widget, QtWidgets.QCheckBox):
                settings[key] = widget.isChecked()
            elif isinstance(widget, QtWidgets.QLineEdit):
                settings[key] = widget.text().strip()
            elif isinstance(widget, QtWidgets.QComboBox):
                settings[key] = widget.currentData()
            else:
                settings[key] = widget.value()
        return settings

    def save_settings(self):
        try:
            config.save(self.collect_settings(), self._settings_file)
        except OSError as exc:
            self.log("No se pudieron guardar los ajustes: {}".format(exc), "warning")

    # ------------------------------------------------------------------ acciones

    def log(self, message, level="info"):
        text = html.escape(message).replace("\n", "<br>")
        color = LOG_COLORS.get(level)
        if color:
            text = '<span style="color:{}">{}</span>'.format(color, text)
        now = stamp()
        self.log_view.appendHtml('<span style="color:#888">{}</span>&nbsp; {}'.format(now, text))
        print("[MBL Retopo UV {}] ".format(now) + message)
        QtWidgets.QApplication.processEvents()

    def _browse_executable(self):
        if cmds.about(macOS=True):
            # en macOS el .app es una carpeta: dejamos elegir cualquier archivo o el bundle
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Elige Instant Meshes.app", "/Applications")
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Elige Instant Meshes", "",
                                                            "Ejecutable (*.exe);;Todos (*)")
        if path:
            self.widgets["instant_meshes_path"].setText(path)
            if not config.resolve_executable(path):
                self.log("Esa ruta no parece contener Instant Meshes.", "warning")
            self.save_settings()

    def _analyze(self):
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona al menos una malla poligonal.", "warning")
            return
        found = []
        for mesh in meshes:
            try:
                report = cleanup.analyze(mesh)
            except MBLError as exc:
                self.log(str(exc), "error")
                continue
            cleanup.log_report(mesh, report, self.log)
            found.extend(cleanup.problem_components(report))
        if found:
            cmds.select(found, replace=True)
            self.log("Componentes problemáticos seleccionados.")

    def _recommend_faces(self):
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona al menos una malla poligonal.", "warning")
            return
        from mbl_engine.analyze import thousands
        requested = int(self.widgets["retopo_target_faces"].value())
        notes = []
        for mesh in meshes:
            try:
                figures, warning = retopo.recommend_faces(mesh, requested)
            except Exception as exc:
                self.log("{}: no se pudo medir ({}).".format(short_name(mesh), exc), "warning")
                continue
            self.log("{}: caras recomendadas: al menos {} (máximo detalle {}; {:.0f} % de láminas finas que el motor engorda)."
                     .format(short_name(mesh), thousands(figures["minimum"]), thousands(figures["detail"]),
                             figures["sheet_percent"]))
            if warning:
                self.log("{}: {}".format(short_name(mesh), warning), "warning")
            notes.append("Mínimo recomendado: {} · Máximo detalle: {}{}".format(
                thousands(figures["minimum"]), thousands(figures["detail"]),
                "" if len(meshes) == 1 else " ({})".format(short_name(mesh))))
        self.faces_note.setText(" | ".join(notes))

    def _diagnose(self):
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona el retopo que quieres medir.", "warning")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for mesh in meshes:
                try:
                    diagnostics.diagnose(mesh, log=self.log)
                except MBLError as exc:
                    self.log(str(exc), "error")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------ guías y detalle

    def _toggle_checker(self):
        """«Ver cuadrícula»: material de cuadros temporal en la malla seleccionada (otro clic lo quita)."""
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona la malla en la que ver la cuadrícula.", "warning")
            return
        from . import uv
        for mesh in meshes:
            try:
                cmds.undoInfo(openChunk=True)
                shown = uv.toggle_checker(mesh)
            except MBLError as exc:
                self.log(str(exc), "error")
                continue
            finally:
                cmds.undoInfo(closeChunk=True)
            self.log("{}: cuadrícula {}.".format(short_name(mesh), "puesta (tecla 6 en el visor para verla)" if shown
                                                 else "quitada (material anterior)"))

    def _source_mesh(self):
        """Malla original sobre la que se dibuja o pinta: la seleccionada, o su original si es un retopo."""
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona el modelo original.", "warning")
            return None
        mesh = meshes[0]
        source = diagnostics.find_source(mesh)
        if source and cmds.objExists(source):
            self.log("{} es un retopo: se usa su original, {}.".format(short_name(mesh), short_name(source)))
            cmds.setAttr(source + ".visibility", 1)
            mesh = source
        return mesh

    @staticmethod
    def _curve_transforms(nodes):
        return [n for n in cmds.ls(nodes, long=True, type="transform") or []
                if cmds.listRelatives(n, shapes=True, type="nurbsCurve")]

    def _toggle_guides(self):
        if self._guide_session is None:
            mesh = self._source_mesh()
            if not mesh:
                return
            existing = set(self._curve_transforms(cmds.ls(type="transform", long=True)))
            cmds.select(clear=True)
            cmds.makeLive(mesh)
            if not cmds.pencilCtx("mblGuidePencil", exists=True):
                cmds.pencilCtx("mblGuidePencil")
            cmds.setToolTo("mblGuidePencil")
            self._guide_session = (mesh, existing)
            self.guide_button.setText("Terminar guías")
            self.log("Dibuja los trazos sobre {} (se pegan a la superficie). Pulsa 'Terminar guías' al acabar."
                     .format(short_name(mesh)))
            return
        mesh, existing = self._guide_session
        self._guide_session = None
        self.guide_button.setText("Dibujar guías")
        cmds.makeLive(none=True)
        cmds.setToolTo("selectSuperContext")
        new = [c for c in self._curve_transforms(cmds.ls(type="transform", long=True)) if c not in existing]
        if not cmds.objExists(mesh):
            return
        added = guides.add_curves(mesh, new)
        self.log("{}: {} guía(s) nuevas, {} en total. El próximo retopo las seguirá.".format(
            short_name(mesh), len(added), len(guides.guide_curves(mesh))))

    def _use_selected_curves(self):
        selection = cmds.ls(selection=True, long=True) or []
        curves = self._curve_transforms(selection)
        mesh = self._source_mesh()
        if not mesh or not curves:
            self.log("Selecciona el modelo y una o varias curvas.", "warning")
            return
        guides.add_curves(mesh, curves)
        self.log("{}: {} curva(s) añadidas como guías.".format(short_name(mesh), len(curves)))

    def _clear_guides(self):
        mesh = self._source_mesh()
        if mesh and guides.clear_guides(mesh):
            self.log("{}: guías borradas.".format(short_name(mesh)))

    def _paint_detail(self):
        mesh = self._source_mesh()
        if not mesh:
            return
        detail.start_paint(mesh)
        self.log("Pinta en rojo las zonas que necesitan más caras sobre {}. Cuando acabes, pulsa 'Retopo'."
                 .format(short_name(mesh)))

    def _reset_defaults(self):
        """Todos los ajustes vuelven a su valor de fábrica (menos la ruta de Instant Meshes, que es del equipo)."""
        settings = dict(config.DEFAULTS, instant_meshes_path=self.widgets["instant_meshes_path"].text().strip())
        self.apply_settings(settings)
        self.save_settings()
        self.log("Todos los ajustes vuelven a sus valores por defecto.")

    def _clear_detail(self):
        mesh = self._source_mesh()
        if mesh and detail.clear_detail(mesh):
            self.log("{}: detalle pintado borrado.".format(short_name(mesh)))

    # ------------------------------------------------------------------ semáforo

    def _dot(self, level):
        pixmap = QtGui.QPixmap(12, 12)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(status.LEVEL_COLORS[level]))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        return QtGui.QIcon(pixmap)

    def show_status(self, statuses):
        """Pinta el semáforo: {malla: resultado de status.evaluate()}. Abre lo que no está en verde."""
        self.status_tree.clear()
        for mesh, results in statuses.items():
            level = status.overall(results)
            summary = "   ".join("{} {}".format(status.STEP_LABELS[r["step"]], status.LEVEL_NAMES[r["level"]])
                                 for r in results)
            top = QtWidgets.QTreeWidgetItem(["{}  —  {}".format(short_name(mesh), summary)])
            top.setIcon(0, self._dot(level))
            top.setData(0, QtCore.Qt.UserRole, mesh)
            self.status_tree.addTopLevelItem(top)
            for r in results:
                step_item = QtWidgets.QTreeWidgetItem(["{}: {}".format(status.STEP_LABELS[r["step"]],
                                                                       status.LEVEL_NAMES[r["level"]])])
                step_item.setIcon(0, self._dot(r["level"]))
                top.addChild(step_item)
                for c in r["checks"]:
                    check_item = QtWidgets.QTreeWidgetItem([c["text"]])
                    check_item.setIcon(0, self._dot(c["level"]))
                    step_item.addChild(check_item)
                    if c["advice"]:
                        advice = QtWidgets.QTreeWidgetItem(["→ " + c["advice"]])
                        advice.setForeground(0, QtGui.QBrush(QtGui.QColor("#a0a0a0")))
                        advice.setToolTip(0, c["advice"])
                        check_item.addChild(advice)
                        check_item.setExpanded(c["level"] != status.GREEN)
                step_item.setExpanded(r["level"] != status.GREEN)
            top.setExpanded(True)

    def _select_status_mesh(self, item, column):
        while item.parent():
            item = item.parent()
        mesh = item.data(0, QtCore.Qt.UserRole)
        if mesh and cmds.objExists(mesh):
            cmds.select(mesh, replace=True)

    def _check_status(self):
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona las mallas que quieres comprobar.", "warning")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        statuses = {}
        try:
            settings = self.collect_settings()
            for mesh in meshes:
                try:
                    statuses[mesh] = status.evaluate(mesh, settings=settings)
                    status.log_status(mesh, statuses[mesh], self.log)
                except MBLError as exc:
                    self.log(str(exc), "error")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.show_status(statuses)

    def _clear_pole_colors(self):
        meshes = selected_mesh_transforms()
        cleared = [m for m in meshes if diagnostics.clear_pole_colors(m)]
        self.log("Colores de polos quitados de {} malla(s).".format(len(cleared)))

    def _request_cancel(self):
        self._cancel_requested = True
        self.status.setText("Cancelando...")

    def _progress(self, done, total, text):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        if text:
            self.status.setText(text)
        QtWidgets.QApplication.processEvents()

    def run_steps(self, steps):
        if self._running:
            return
        meshes = selected_mesh_transforms()
        if not meshes:
            self.log("Selecciona al menos una malla poligonal.", "warning")
            return
        settings = self.collect_settings()
        self.save_settings()

        self._running = True
        self._cancel_requested = False
        for button in self.run_buttons:
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        self.log("— {} en {} malla(s) —".format(" → ".join(pipeline.STEP_LABELS[s] for s in steps), len(meshes)))
        began = time.time()
        outcome = None
        try:
            outcome = pipeline.run(meshes, steps, settings, self.log, self._progress,
                                   lambda: self._cancel_requested)
        except MBLError as exc:
            self.log(str(exc), "error")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            for button in self.run_buttons:
                button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self._running = False

        if outcome is None:
            return
        self.show_status(outcome.get("status", {}))
        errors = outcome["errors"]
        ok = len(outcome["results"])
        elapsed = format_duration(time.time() - began)
        self.status.setText("Listo: {} correcta(s), {} con error ({}).".format(ok, len(errors), elapsed))
        self.log("Listo: {} correcta(s), {} con error. Tiempo total: {}. Ctrl+Z deshace todo el proceso.".format(
            ok, len(errors), elapsed))
        if errors and not (len(errors) == 1 and errors[0][1] == "Cancelado"):
            details = "\n\n".join("{}:\n{}".format(short_name(mesh), message) for mesh, message in errors)
            QtWidgets.QMessageBox.warning(self, "MBL Retopo UV", details)

    def closeEvent(self, event):
        if self._running:
            self._request_cancel()
            event.ignore()
            return
        self.save_settings()
        super(RetopoUVWindow, self).closeEvent(event)


_window = None


def show():
    """Abre la ventana. Si ya estaba abierta, la cierra y crea una nueva."""
    global _window
    parent = maya_main_window()
    if parent is not None:
        for child in parent.findChildren(QtWidgets.QDialog, WINDOW_OBJECT_NAME):
            child.close()
            child.deleteLater()
    _window = RetopoUVWindow(parent)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
