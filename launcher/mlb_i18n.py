# SPDX-License-Identifier: GPL-2.0-or-later
"""Translations of the MLB launcher: its own texts first, then the shared Uncharted/bbport tables
(the texts both launchers use are translated there). English is the fallback."""
import ctypes
import locale

import i18n_base

LANGUAGES = i18n_base.LANGUAGES


def language_code(value=''):
    """The chosen interface language, else the Windows display language (locale.getlocale() gives
    names such as "Spanish_Venezuela" there, not "es_VE"), else English."""
    if value in {code for code, _name in LANGUAGES if code}:
        return value
    name = ''
    try:
        name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
    except (AttributeError, OSError):
        pass
    code = (name or locale.getlocale()[0] or 'en').split('_', 1)[0].lower()
    return code if code in TRANSLATIONS or code in i18n_base.TRANSLATIONS else 'en'

TRANSLATIONS = {
    'es': {
        '5.1 surround': 'Envolvente 5.1',
        '60 FPS (as on PS4)': '60 FPS (como en PS4)',
        '7.1 surround': 'Envolvente 7.1',
        'A / Cross': 'A / Cruz',
        'Adjust crop': 'Ajustar recorte',
        'All': 'Todos',
        'All files': 'Todos los archivos',
        'Automatic (the Windows speaker setup)': 'Automático (la configuración de altavoces de Windows)',
        'B / Circle': 'B / Círculo',
        'Banner': 'Cabecera',
        'Based on {}.': 'Basado en {}.',
        'Cancel': 'Cancelar',
        'Change image': 'Cambiar imagen',
        'Checking vibration support…': 'Comprobando la vibración…',
        'Choose image': 'Elegir imagen',
        'Circle ○': 'Círculo ○',
        'Click a box, then press a key. ✕ removes a binding. Each PS4 button can have two keys. Insert opens the in-game settings menu.': 'Haz clic en una casilla y pulsa una tecla. ✕ quita la asignación. Cada botón de PS4 puede tener dos teclas. Insert abre el menú de ajustes dentro del juego.',
        'Connect a controller, then detect again.': 'Conecta un mando y vuelve a detectarlo.',
        'Connected · Vibration not supported': 'Conectado · Sin vibración',
        'Connected · Vibration supported': 'Conectado · Con vibración',
        'Connected · Vibration test completed': 'Conectado · Prueba de vibración completada',
        'Connected · Vibration test failed': 'Conectado · La prueba de vibración falló',
        'Controller vibration': 'Vibración del mando',
        'Could not clear the shader cache: {}': 'No se pudo borrar la caché de shaders: {}',
        'Could not detect the controller: {}': 'No se pudo detectar el mando: {}',
        'Could not save the input configuration: {}': 'No se pudo guardar la configuración de controles: {}',
        'Could not stop the game: {}': 'No se pudo detener el juego: {}',
        'Cover': 'Portada',
        'Create shortcut': 'Crear acceso directo',
        'Cross ✕': 'Cruz ✕',
        'Crowd textures': 'Texturas del público',
        'D-pad down': 'Cruceta abajo',
        'D-pad left': 'Cruceta izquierda',
        'D-pad right': 'Cruceta derecha',
        'D-pad up': 'Cruceta arriba',
        'Detect controller': 'Detectar mando',
        'Detecting…': 'Detectando…',
        'Double-click the cover to play. Click the banner, or right-click the cover, to use your own image.': 'Doble clic en la portada para jugar. Haz clic en la cabecera, o clic derecho en la portada, para usar tu propia imagen.',
        'Drag an image here or click Choose image.': 'Arrastra una imagen aquí o pulsa Elegir imagen.',
        'Each fan picks its texture from a table. Off: the crowd is black.': 'Cada aficionado elige su textura de una tabla. Desactivado: el público se ve negro.',
        'Earned': 'Conseguidos',
        'Every trophy is earned.': 'Has conseguido todos los trofeos.',
        'FIFO Relaxed': 'FIFO relajado',
        'FSR 1 (AMD FidelityFX, sharp)': 'FSR 1 (AMD FidelityFX, nítido)',
        'Fixes of this port for MLB The Show. Leave them on; turn one off only to compare.': 'Arreglos de este port para MLB The Show. Déjalos activados; desactiva uno solo para comparar.',
        'Game controller': 'Mando',
        'Graphics fixes': 'Arreglos gráficos',
        'Header': 'Cabecera',
        'Hidden trophy': 'Trofeo oculto',
        'High': 'Alta',
        'Horizontal position': 'Posición horizontal',
        'Image files': 'Imágenes',
        'Immediate (tearing)': 'Inmediato (con tearing)',
        'Insert opens the in-game settings menu.': 'Insert abre el menú de ajustes dentro del juego.',
        'Keep playing to find out how to earn it.': 'Sigue jugando para descubrir cómo conseguirlo.',
        'Keyboard': 'Teclado',
        'Keyboard and controller. They work together; changes apply when the game starts.': 'Teclado y mando. Funcionan a la vez; los cambios se aplican al iniciar el juego.',
        'Left stick click / L3': 'Clic stick izquierdo / L3',
        'Left stick down': 'Stick izquierdo abajo',
        'Left stick left': 'Stick izquierdo a la izquierda',
        'Left stick right': 'Stick izquierdo a la derecha',
        'Left stick up': 'Stick izquierdo arriba',
        'Locked': 'Bloqueados',
        'Low': 'Baja',
        'Mailbox (low latency, no tearing)': 'Mailbox (poca latencia, sin tearing)',
        'Maximum': 'Máxima',
        'Medium': 'Media',
        'No controller detected': 'No se detectó ningún mando',
        'No trophy list yet. Set the game folder on the Game page (it needs sce_sys/trophy/trophy00.trp).': 'Aún no hay lista de trofeos. Elige la carpeta del juego en la página Juego (necesita sce_sys/trophy/trophy00.trp).',
        'Not assigned': 'Sin asignar',
        'Not available': 'No disponible',
        'Nothing here yet.': 'Todavía no hay nada aquí.',
        'Off': 'Desactivado',
        'Original icon': 'Icono original',
        'PC PORT': 'PORT PARA PC',
        'PC port': 'port para PC',
        'Picture': 'Imagen',
        'Portrait': 'Vertical',
        'Ready to play. The first start prepares the game and compiles shaders; it can stutter for a while.': 'Listo para jugar. El primer inicio prepara el juego y compila shaders; puede ir a tirones un rato.',
        'Restore': 'Restaurar',
        'Right stick click / R3': 'Clic stick derecho / R3',
        'Right stick down': 'Stick derecho abajo',
        'Right stick left': 'Stick derecho a la izquierda',
        'Right stick right': 'Stick derecho a la derecha',
        'Right stick up': 'Stick derecho arriba',
        'Save changes': 'Guardar cambios',
        'See-through nets': 'Redes transparentes',
        'Share / Mic (misc)': 'Share / Micro (misc)',
        'Shortcut icon': 'Icono del acceso directo',
        'Shortcut name': 'Nombre del acceso directo',
        'Smooth (bilinear)': 'Suave (bilineal)',
        'Speakers': 'Altavoces',
        'Square □': 'Cuadrado □',
        'Status': 'Estado',
        'Stereo': 'Estéreo',
        'Stopping the game…': 'Deteniendo el juego…',
        'Test vibration': 'Probar vibración',
        'Testing…': 'Probando…',
        'The folder with eboot.bin, PS4\\ and sce_sys\\.': 'La carpeta con eboot.bin, PS4\\ y sce_sys\\.',
        'The game draws at 1920 × 1080; the window (or the screen in fullscreen) shows that picture scaled to fit.': 'El juego dibuja a 1920 × 1080; la ventana (o la pantalla en pantalla completa) muestra esa imagen escalada.',
        'The game is in your Steam library. It starts with the settings saved in this launcher.': 'El juego está en tu biblioteca de Steam. Se inicia con los ajustes guardados en este launcher.',
        'The game runs at 60 FPS on PS4. 30 FPS helps slower PCs.': 'En PS4 el juego va a 60 FPS. 30 FPS ayuda en PCs más lentos.',
        'The game runs offline: online modes (Diamond Dynasty online, Ranked, friends) need PSN servers and are not available.': 'El juego funciona sin conexión: los modos online (Diamond Dynasty online, Ranked, amigos) necesitan los servidores de PSN y no están disponibles.',
        "The game's own trophy list. A trophy shows here, and as a notice in the game, when you earn it.": 'La lista de trofeos del juego. Un trofeo aparece aquí, y como aviso en el juego, cuando lo consigues.',
        'The language the PS4 would be set to; the game uses it for text and commentary.': 'El idioma que tendría la PS4; el juego lo usa para los textos y los comentarios.',
        'The pictures come from your game folder. Choose an image to use your own.': 'Las imágenes salen de la carpeta del juego. Elige una imagen para usar la tuya.',
        'The protective nets let the field and the fans show through. Off: they are gray walls.': 'Las redes de protección dejan ver el campo y el público. Desactivado: se ven como paredes grises.',
        'The setting applies the next time the game starts.': 'El ajuste se aplica la próxima vez que se inicie el juego.',
        'This image could not be opened: {}': 'No se pudo abrir esta imagen: {}',
        'Touchpad': 'Panel táctil',
        'Touchpad click': 'Clic del panel táctil',
        'Triangle △': 'Triángulo △',
        'Trophies': 'Trofeos',
        'Unavailable while the game is running.': 'No disponible mientras el juego está abierto.',
        'Vertical position': 'Posición vertical',
        'Vibration': 'Vibración',
        'Vibration intensity': 'Intensidad de la vibración',
        'What the game is told the speakers are. Automatic follows the Windows speaker configuration of the default playback device.': 'Los altavoces que se le indican al juego. Automático sigue la configuración de altavoces de Windows del dispositivo de reproducción predeterminado.',
        'X / Square': 'X / Cuadrado',
        'Y / Triangle': 'Y / Triángulo',
        'Your own decrypted dump of MLB The Show 24 ({}).': 'Tu propio volcado descifrado de MLB The Show 24 ({}).',
        'bb-probe.exe was not found.': 'No se encontró bb-probe.exe.',
        'controller probe failed': 'falló la detección del mando',
        'taskkill exited with code {}': 'taskkill terminó con el código {}',
        '{} of {} earned': '{} de {} conseguidos',
    },
}


def translate(language, text):
    own = TRANSLATIONS.get(language, {}).get(text)
    return own if own else i18n_base.translate(language, text)
