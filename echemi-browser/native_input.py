"""Calibrated pointer transport for the browser service dedicated Xvfb display."""
import asyncio
import ctypes as C
import ctypes.util


class X11Mouse:
    def __init__(self):
        libraries = [ctypes.util.find_library(name) for name in ('X11', 'Xtst')]
        if not all(libraries):
            raise RuntimeError('Native pointer requires libX11 and libXtst')
        self.xlib, self.xtst = [C.CDLL(path) for path in libraries]
        self.xlib.XOpenDisplay.argtypes = [C.c_char_p]
        self.xlib.XOpenDisplay.restype = C.c_void_p
        self.xlib.XSync.argtypes = [C.c_void_p, C.c_int]
        self.xlib.XSync.restype = C.c_int
        self.xlib.XCloseDisplay.argtypes = [C.c_void_p]
        self.xlib.XCloseDisplay.restype = C.c_int
        self.xtst.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
        self.xtst.XTestFakeMotionEvent.restype = C.c_int
        self.xtst.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.xtst.XTestFakeButtonEvent.restype = C.c_int
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('Cannot open isolated X display')
        self.offset = None
        self.x, self.y = 0, 0
        self.pressed = False
        self.metrics = {'backend': 'x11_xtest', 'moves': 0, 'downs': 0, 'ups': 0}

    def screen_move(self, x, y):
        if not (0 <= x < 1600 and 0 <= y < 1200):
            raise ValueError('Pointer outside dedicated display')
        if not self.xtst.XTestFakeMotionEvent(self.display, -1, round(x), round(y), 0):
            raise RuntimeError('XTEST motion failed')
        self.xlib.XSync(self.display, 0)

    async def calibrate(self, page):
        # This page is about:blank with our own content, before any Echemi visit.
        if page.url != 'about:blank':
            raise ValueError('Calibration requires a new blank page')
        await page.set_content('<style>body{margin:0;height:1500px;user-select:none}</style><div>Pointer calibration</div>')
        await page.bring_to_front()
        await page.evaluate("""() => {
            window._pointerEvents=[];
            for(const type of ['mousemove','mousedown','mouseup'])
                addEventListener(type,e=>window._pointerEvents.push({type:e.type,x:e.clientX,y:e.clientY,sx:e.screenX,sy:e.screenY,buttons:e.buttons,trusted:e.isTrusted,t:performance.now()}),{passive:true});
        }""")
        geometry = await page.evaluate('({inner:[innerWidth,innerHeight],outer:[outerWidth,outerHeight],dpr:devicePixelRatio})')
        if geometry['dpr'] != 1:
            raise ValueError('Native pointer requires devicePixelRatio=1')
        offsets = []
        for sx, sy in [(400,450),(600,600),(1000,800)]:
            self.screen_move(sx,sy)
            await page.wait_for_function('([sx,sy])=>window._pointerEvents.some(e=>e.type==="mousemove"&&e.sx===sx&&e.sy===sy)',arg=[sx,sy],timeout=3000)
            event = await page.evaluate('window._pointerEvents.at(-1)')
            if not event['trusted']:
                raise ValueError('Untrusted calibration event')
            offsets.append((sx-event['x'],sy-event['y']))
        if len(set(offsets)) != 1:
            raise ValueError('Inconsistent screen-to-viewport coordinates')
        self.offset = offsets[0]
        self.metrics['calibration'] = {'offset':list(self.offset),'geometry':geometry,'points_checked':3}
        await self.move(420,320)
        await self.down()
        await asyncio.sleep(.1)
        await self.move(700,320)
        await self.up()
        await page.wait_for_function('window._pointerEvents.some(e=>e.type==="mouseup")',timeout=3000)
        events = await page.evaluate('window._pointerEvents.filter(e=>e.type!=="mousemove")')
        if (len(events)!=2 or events[0]['type']!='mousedown' or events[0]['buttons']!=1
                or events[1]['type']!='mouseup' or events[1]['buttons']!=0
                or [events[0]['x'],events[0]['y']] != [420,320]
                or [events[1]['x'],events[1]['y']] != [700,320]
                or not all(e['trusted'] for e in events)):
            raise ValueError('Native button calibration failed')
        self.metrics['calibration']['button_events'] = events
        self.metrics.update(moves=0,downs=0,ups=0)
        return self.metrics['calibration']

    async def move(self,x,y,**kwargs):
        if self.offset is None:
            raise RuntimeError('Pointer is not calibrated')
        if not (0 <= x < 1280 and 0 <= y < 900):
            raise ValueError('Pointer outside calibrated viewport')
        self.screen_move(x+self.offset[0],y+self.offset[1])
        self.x, self.y = x, y
        self.metrics['moves'] += 1
        await asyncio.sleep(0)

    async def down(self,**kwargs):
        if not self.xtst.XTestFakeButtonEvent(self.display,1,1,0):
            raise RuntimeError('XTEST button press failed')
        self.xlib.XSync(self.display,0)
        self.pressed=True
        self.metrics['downs']+=1
        await asyncio.sleep(0)

    async def up(self,**kwargs):
        if not self.xtst.XTestFakeButtonEvent(self.display,1,0,0):
            raise RuntimeError('XTEST button release failed')
        self.xlib.XSync(self.display,0)
        self.pressed=False
        self.metrics['ups']+=1
        await asyncio.sleep(0)

    async def wheel(self,dx,dy):
        for delta,positive,negative in [(dy,5,4),(dx,7,6)]:
            for _ in range(round(abs(delta)/120)):
                button=positive if delta>0 else negative
                self.xtst.XTestFakeButtonEvent(self.display,button,1,0)
                self.xtst.XTestFakeButtonEvent(self.display,button,0,0)
                self.xlib.XSync(self.display,0)
                await asyncio.sleep(.02)

    def close(self):
        if self.display:
            if self.pressed:
                self.xtst.XTestFakeButtonEvent(self.display,1,0,0)
                self.xlib.XSync(self.display,0)
            self.xlib.XCloseDisplay(self.display)
            self.display=None


class InputPage:
    def __init__(self,page,mouse):
        self._page=page
        self.mouse=mouse

    def __getattr__(self,name):
        return getattr(self._page,name)

    async def close(self, **kwargs):
        self.mouse.close()
        await self._page.close(**kwargs)


async def prepare_native_page(page):
    """Measure actual toolbar offset; emulated innerHeight is not a measurement."""
    mouse = X11Mouse()
    session = None
    try:
        session = await page.context.new_cdp_session(page)
        target = await session.send('Target.getTargetInfo')
        window = await session.send('Browser.getWindowForTarget',
                                    {'targetId': target['targetInfo']['targetId']})
        await mouse.calibrate(page)
        await session.send('Browser.setWindowBounds', {
            'windowId': window['windowId'], 'bounds': {
                'left': 0, 'top': 0, 'width': 1280 + mouse.offset[0],
                'height': 900 + mouse.offset[1], 'windowState': 'normal'}})
        await asyncio.sleep(.3)
        await mouse.calibrate(page)
        await mouse.move(664, 400)
        return InputPage(page, mouse)
    except BaseException:
        mouse.close()
        raise
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass  # Closing CDP must not leak the native display or hide calibration errors.
