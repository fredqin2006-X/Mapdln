"""Desktop workbench, accounts and responsive background process control."""
import concurrent.futures
import json
import sys
import uuid
from pathlib import Path
from string import Template
from datetime import datetime, timezone
from PySide6.QtCore import QObject,Signal,Slot,QUrl,QProcess,QTimer,Qt,QStandardPaths
from PySide6.QtGui import QDesktopServices,QFont,QIcon,QPixmap,QPalette,QColor
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox,QStackedWidget,QScrollArea,
    QPlainTextEdit,QProgressBar,QFileDialog,QSplitter,QCheckBox,QAbstractSpinBox,
    QGridLayout,QSizePolicy,QStyledItemDelegate,QFrame)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtNetwork import QNetworkProxy
from . import __version__
from .config import load,save,redact,SECRETS,Ledger
from .geometry import make_grid,from_wgs
from .engine import estimate,Control,fetch_tile,FABDEM_BASE,session_for
from .server import PreviewServer
from .sources import get_source,list_sources,credentials_for,secret_values,build_request
from .local_dem import inspect_dem,snapshot_dem


class Jobs(QObject):
    finished=Signal(int,object,object)
    def __init__(self,parent):
        super().__init__(parent)
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.serial=0;self.callbacks={};self.finished.connect(self.receive)
    def run(self,work,callback):
        self.serial+=1;key=self.serial;self.callbacks[key]=callback
        def run():
            try:result,error=work(),None
            except Exception as exc:result,error=None,exc
            self.finished.emit(key,result,error)
        self.pool.submit(run)
    @Slot(int,object,object)
    def receive(self,key,result,error):
        callback=self.callbacks.pop(key,None)
        if callback:callback(result,error)


class Page(QWebEnginePage):
    serviceAlert=Signal(str)
    def javaScriptAlert(self,origin,message):
        self.serviceAlert.emit(message)
    def javaScriptConsoleMessage(self,level,message,line,source):
        if any(x in message.upper() for x in ('INVALID_USER','KEY异常','USERKEY_PLAT','INVALID_USER_SCODE','AK有误','授权失败')):
            self.serviceAlert.emit('地图授权错误：请检查 Key / AK 类型、安全密钥配对、Referer 限制和服务权限')


class Bridge(QObject):
    def __init__(self,window,test=False):
        super().__init__(window);self.window=window;self.test=test
    @Slot()
    def pageReady(self):
        w=self.window;provider=w.test_provider if self.test else w.preview_provider()
        view=w.test_view if self.test else w.web
        view.page().runJavaScript("setTheme("+json.dumps(w.theme_dark)+");setSelectionLocked("+json.dumps(False if self.test else w.selection_locked)+");previewProvider("+json.dumps(provider)+")")
    @Slot(str)
    def providerReady(self,system):
        if not self.test:
            w=self.window;w.display_system=system;w.render_selection();w.render_dem()
            if w.grid:
                p=w.grid.geographic.centroid
                w.web.page().runJavaScript('setCenter('+json.dumps(from_wgs([p.x,p.y],system))+')')
    @Slot(str,bool)
    def mapStatus(self,message,ok):
        w=self.window;text=redact(message,secret_values({k:w.settings.get(k) for k in SECRETS}))
        if self.test:w.test_status.setText(text);w.test_success=ok
        else:w.map_status.setText(text)
    @Slot(str)
    def selectRegion(self,text):
        if self.test or self.window.selection_locked:return
        w=self.window
        try:
            selection=json.loads(text);grid=make_grid(selection,w.resolution.value())
            w.selection=selection;w.grid=grid;w.plan=None;w.render_selection();w.replan()
        except Exception as exc:
            w.map_status.setText("选区需要修正："+str(exc));w.plan=None;w.selection=json.loads(text);w.start.setEnabled(False)
            w.plan_generation+=1;w.debounce.stop()
            w.web.page().runJavaScript('updateSelection('+json.dumps({'shape':w.selection.get('shape','polygon'),'controls':w.selection.get('points',[]),'boundary':[]})+')')
    @Slot()
    def clearRegion(self):
        if self.test or self.window.selection_locked:return
        w=self.window;w.selection=None;w.grid=None;w.plan=None;w.start.setEnabled(False)
        w.plan_generation+=1
        w.debounce.stop()
        w.show_estimate(None,"绘制选区或输入 WGS84 中心坐标后预判额度")
        w.area_label.setText("尚未选择区域。支持矩形、圆形、多边形和闭合插值曲线。")
        w.web.page().runJavaScript("updateSelection({controls:[],boundary:[]})")


def label(text,wrap=True,role=None,muted=False):
    w=QLabel(text);w.setWordWrap(wrap)
    if role:w.setProperty("role",role)
    if muted:w.setProperty("muted",True)
    return w
def button(text,callback,primary=False,**props):
    w=QPushButton(text);w.clicked.connect(callback);w.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:w.setProperty("primary",True)
    for key,value in props.items():w.setProperty(key,value)
    return w
def repolish(w):
    w.style().unpolish(w);w.style().polish(w)
def card(title=None,caption=None,name="card"):
    """Rounded surface with an optional heading; returns (frame, body layout)."""
    frame=QFrame();frame.setObjectName(name);body=QVBoxLayout(frame);body.setContentsMargins(16,14,16,16);body.setSpacing(10)
    if title:
        head=QVBoxLayout();head.setSpacing(2);head.addWidget(label(title,False,"title"))
        if caption:head.addWidget(label(caption,True,muted=True))
        body.addLayout(head)
    return frame,body
def divider():
    line=QFrame();line.setObjectName("divider");line.setFixedHeight(1);return line
def resource(name):
    return Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))/"mapdln"/name


class FlatCombo(QComboBox):
    def __init__(self):
        super().__init__()
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.view().setSpacing(3)
        popup=self.view().window();popup.setObjectName('comboPopup')
        if isinstance(popup,QFrame):popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint,True)
    def wheelEvent(self,event):
        if self.hasFocus():super().wheelEvent(event)
        else:event.ignore()
    def showPopup(self):
        width=max(self.width(),max((self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())),default=0)+48)
        self.view().setMinimumWidth(width)
        super().showPopup()


class FlatNumber(QDoubleSpinBox):
    def wheelEvent(self,event):
        if self.hasFocus():super().wheelEvent(event)
        else:event.ignore()


class FlatInteger(QSpinBox):
    def wheelEvent(self,event):
        if self.hasFocus():super().wheelEvent(event)
        else:event.ignore()


class DemDrop(QLineEdit):
    fileDropped=Signal(str)
    def __init__(self):
        super().__init__();self.setReadOnly(True);self.setAcceptDrops(True)
        self.setPlaceholderText("拖入单波段 GeoTIFF 或选择文件")
    def dragEnterEvent(self,event):
        urls=event.mimeData().urls()
        if self.isEnabled() and len(urls)==1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ('.tif','.tiff'):
            event.acceptProposedAction()
    def dropEvent(self,event):
        urls=event.mimeData().urls()
        if len(urls)==1 and urls[0].isLocalFile():
            self.fileDropped.emit(urls[0].toLocalFile());event.acceptProposedAction()


def combo(items):
    w=FlatCombo()
    for title,value in items:w.addItem(title,value)
    return w
def number(low,high,value,decimals=2):
    w=FlatNumber();w.setRange(low,high);w.setDecimals(decimals);w.setValue(value);w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons);return w
def integer(low,high,value):
    w=FlatInteger();w.setRange(low,high);w.setValue(value);w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons);return w


def grid_field(grid,row,column,title,field):
    box=QWidget();layout=QVBoxLayout(box);layout.setContentsMargins(0,0,0,0);layout.setSpacing(5)
    layout.addWidget(label(title,False,muted=True));layout.addWidget(field)
    grid.addWidget(box,row,column)


def form_layout():
    # A grid (rather than QFormLayout) keeps labels vertically centred on styled fields.
    form=QGridLayout();form.setContentsMargins(0,0,0,0);form.setHorizontalSpacing(14);form.setVerticalSpacing(10);form.setColumnStretch(1,1);form._rows=0
    return form
def form_row(form,title,field):
    row=form._rows;form._rows+=1
    form.addWidget(label(title,False,muted=True),row,0,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
    if isinstance(field,QHBoxLayout):form.addLayout(field,row,1)
    else:form.addWidget(field,row,1)


GUIDES={
 "mapbox":("Mapbox 卫星下载",[("mapbox_id","Mapbox ID",False),("mapbox_token","Access Token",True)],
  "https://console.mapbox.com/account/access-tokens/","https://docs.mapbox.com/accounts/guides/tokens/",
  "① 打开控制台，注册或登录。\n② 复制可用的 pk. 公共 Token；试用账户可用默认 Token，不要求新建。\n③ 填入 Token、用户名（ID 只用于标识，不需要密码）。\n④ 保存并测试：实际发起 1 次瓦片请求，计入本机记录。\n\n401：Token 无效/已删除。403：检查账户状态、URL 限制。桌面请求不带网页 Referer，不建议伪造 Referer。超时属于网络问题。"),
 "amap":("高德地图预览",[("amap_key","Web 端 JS API Key",True),("amap_secret","securityJsCode 安全密钥",True)],
  "https://console.amap.com/","https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode",
  "① 打开控制台，按要求注册/认证。\n② 创建或选择应用，添加 Web 端（JS API）Key。\n③ 填入同一项配置的 Key、安全密钥并保存。\n④ 在测试区域实际加载地图，使用高德额度，不计入 Mapbox。\n\n勿用 Web 服务/Android/iOS Key。若有域名限制，复制实际本机地址，按控制台要求填写；端口被占用时地址会变化。安全密钥由仅供本机页面的受限转发服务注入。失败时检查 Key 类型、密钥配对、地址限制、授权及网络。"),
 "baidu":("百度地图预览",[("baidu_ak","浏览器端 AK",True)],
  "https://lbsyun.baidu.com/apiconsole/key","https://lbsyun.baidu.com/docs/jsapi?title=193",
  "① 打开控制台，按要求注册/认证。\n② 创建浏览器端应用，启用 JavaScript API 服务。\n③ 按实际本机地址设置 Referer 白名单，不默认使用无限制通配符。\n④ 复制浏览器端 AK，保存后在测试区域实际加载地图。\n\n不填写服务端 AK、SK 或 SN。失败时检查 AK 类型、服务权限、Referer 白名单、授权及网络。使用百度额度，不计入 Mapbox。")}
CAPTIONS={"mapbox":"卫星影像下载 · 开始下载前必需","amap":"地图预览 · 国内默认推荐","baidu":"地图预览 · 国内备选"}
THEMES={
 "light":dict(bg="#F3F5F7",surface="#FFFFFF",field="#F7F9FA",sunken="#EFF2F5",ink="#17212B",muted="#66727F",line="#E1E6EB",strong="#C6CED6",
  accent="#0E9F8C",accent_hover="#0B8676",accent_press="#08705F",on_accent="#FFFFFF",tint="#E3F5F1",danger="#D14343",danger_tint="#FDECEC",
  warn="#A86412",warn_tint="#FDF1DF",term_bg="#0F161B",term_ink="#C9D4DC"),
 "dark":dict(bg="#0E1215",surface="#161B20",field="#1C232A",sunken="#12171B",ink="#E6EBEF",muted="#8C98A4",line="#273039",strong="#3A4652",
  accent="#2EC4AE",accent_hover="#4AD6C1",accent_press="#22A894",on_accent="#06201C",tint="#17332F",danger="#F07171",danger_tint="#3A1F22",
  warn="#F0B44C",warn_tint="#3A2E17",term_bg="#0A0E11",term_ink="#C9D4DC")}
STYLE=Template("""
* { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif; }
QMainWindow, QStackedWidget { background: $bg; }
QWidget { color: $ink; font-size: 13px; }
QLabel, QCheckBox { background: transparent; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
QToolTip { background: $surface; color: $ink; border: 1px solid $line; border-radius: 6px; padding: 6px 8px; }

QFrame#topbar, QFrame#card, QFrame#mapCard { background: $surface; border: 1px solid $line; border-radius: 14px; }
QFrame#stat { background: $sunken; border: 1px solid $line; border-radius: 10px; }
QFrame#segment { background: $sunken; border: 1px solid $line; border-radius: 11px; }
QFrame#divider { background: $line; border: 0; }
QFrame#mapBar { background: transparent; border: 0; }

QLabel#brand { font-size: 19px; font-weight: 700; color: $ink; }
QLabel[muted="true"] { color: $muted; font-size: 12px; }
QLabel[role="title"] { font-size: 14px; font-weight: 600; color: $ink; }
QLabel[role="pageTitle"] { font-size: 22px; font-weight: 600; color: $ink; }
QLabel[role="stat"] { font-size: 16px; font-weight: 600; color: $ink; }
QLabel[role="stage"] { font-size: 13px; font-weight: 600; color: $ink; }
QLabel[role="info"] { font-size: 12px; color: $ink; }
QLabel[role="version"] { background: $tint; color: $accent; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: 600; }
QLabel[role="callout"] { background: $sunken; border: 1px solid $line; border-left: 3px solid $accent; border-radius: 8px; padding: 10px 12px; color: $ink; font-size: 12px; }
QLabel#health { border-radius: 13px; padding: 5px 12px; font-size: 12px; font-weight: 600; }
QLabel#health[state="ok"] { background: $tint; color: $accent; }
QLabel#health[state="warn"] { background: $warn_tint; color: $warn; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit#sourceEditor {
    background: $field; border: 1px solid $line; border-radius: 8px; padding: 6px 10px; min-height: 20px;
    selection-background-color: $accent; selection-color: $on_accent; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: $strong; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit#sourceEditor:focus { border-color: $accent; background: $surface; }
QLineEdit:read-only { color: $muted; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: $muted; background: $sunken; border-color: $line; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 30px; border: 0; background: transparent; }
QComboBox::down-arrow { image: url("$arrow"); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: $surface; border: 1px solid $line; border-radius: 10px; padding: 6px; outline: 0;
    selection-background-color: $tint; selection-color: $accent; }
QComboBox QAbstractItemView::item { min-height: 32px; padding: 4px 10px; border-radius: 6px; margin: 1px 0; }
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover { background: $tint; color: $accent; }
QFrame#comboPopup { border: 0; background: $surface; }

QPushButton { background: $surface; border: 1px solid $line; border-radius: 8px; padding: 6px 14px; min-height: 20px; }
QPushButton:hover { border-color: $accent; color: $accent; }
QPushButton:pressed { background: $tint; }
QPushButton:disabled { color: $muted; background: $sunken; border-color: $line; }
QPushButton[primary="true"] { background: $accent; color: $on_accent; border: 1px solid $accent; font-weight: 600; }
QPushButton[primary="true"]:hover { background: $accent_hover; border-color: $accent_hover; color: $on_accent; }
QPushButton[primary="true"]:pressed { background: $accent_press; border-color: $accent_press; }
QPushButton[primary="true"]:disabled { background: $line; border-color: $line; color: $muted; }
QPushButton[danger="true"]:hover { border-color: $danger; color: $danger; background: $danger_tint; }
QPushButton[large="true"] { padding: 9px 26px; font-size: 14px; }
QPushButton[nav="true"] { background: transparent; border: 0; border-radius: 8px; padding: 6px 20px; color: $muted; font-weight: 600; }
QPushButton[nav="true"]:hover { color: $ink; }
QPushButton[nav="true"]:checked { background: $surface; color: $accent; }
QPushButton[chip="true"] { border-radius: 12px; padding: 3px 0; min-height: 18px; font-size: 12px; background: $field; }
QPushButton[chip="true"]:checked { background: $tint; color: $accent; border-color: $accent; font-weight: 600; }
QPushButton#disclosure { text-align: left; padding: 11px 16px; border-radius: 14px; background: $surface; font-weight: 600; }
QPushButton#disclosure:checked { color: $accent; border-color: $accent; }

QProgressBar { border: 0; border-radius: 7px; background: $sunken; min-height: 14px; max-height: 14px; text-align: center; font-size: 10px; font-weight: 600; color: $ink; }
QProgressBar::chunk { background: $accent; border-radius: 7px; }
QPlainTextEdit#terminal { background: $term_bg; color: $term_ink; border: 1px solid $line; border-radius: 10px; padding: 6px;
    font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; selection-background-color: $accent; selection-color: $on_accent; }

QCheckBox { spacing: 6px; color: $muted; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $strong; border-radius: 4px; background: $field; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent;
    image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iMTAiIHZpZXdCb3g9IjAgMCAxMiAxMCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEgNUw0LjUgOC41TDExIDEiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo=); }

QScrollBar:vertical { border: 0; background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: $strong; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar:horizontal { border: 0; background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: $strong; border-radius: 3px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { border: 0; width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
""")
ADVANCED_TITLE="高级设置 · 坐标、网络与终端日志"


class Window(QMainWindow):
    def __init__(self):
        super().__init__();QApplication.setStyle("Fusion");QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy));self.setWindowTitle("Mapdln · 地图下载器 "+__version__);self.resize(1520,920);self.setMinimumSize(1200,780)
        self.setWindowIcon(QIcon(str(resource('assets/app.ico'))))
        self.settings={};self.config_error=""
        try:self.settings=load()
        except Exception as exc:self.config_error="本机凭据无法解密，请重新保存："+str(exc)
        self.selection=None;self.grid=None;self.plan=None;self.plan_generation=0;self.process=None;self.buffer=b"";self.task_state="idle";self.selection_locked=False
        self.terrain_failed=False;self.display_system="WGS84";self.test_provider="amap";self.test_success=False;self.fields={};self.card_status={};self.dem_info=None;self.dem_generation=0
        self.jobs=Jobs(self)
        assets=resource("web")
        self.server=PreviewServer(assets,lambda:dict(self.settings))
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setContentsMargins(16,14,16,16);layout.setSpacing(14)
        # 顶栏：品牌（左）· 分段导航（中）· 凭据状态（右）
        top=QFrame();top.setObjectName("topbar");header=QHBoxLayout(top);header.setContentsMargins(16,10,16,10);header.setSpacing(12)
        mark=QLabel();mark.setPixmap(QPixmap(str(resource('assets/app.png'))).scaled(36,36,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));header.addWidget(mark)
        title=QVBoxLayout();title.setSpacing(0);brand=QHBoxLayout();brand.setSpacing(8)
        name=label("Mapdln",False);name.setObjectName("brand");brand.addWidget(name);brand.addWidget(label("v"+__version__,False,"version"),0,Qt.AlignmentFlag.AlignVCenter);brand.addStretch()
        title.addLayout(brand);title.addWidget(label("卫星影像与地形数据下载工作台",False,muted=True))
        header.addLayout(title,1)
        nav=QFrame();nav.setObjectName("segment");nav_group=QHBoxLayout(nav);nav_group.setContentsMargins(3,3,3,3);nav_group.setSpacing(2)
        self.work_nav=button("下载工作台",lambda:self.navigate(0),nav=True);self.account_nav=button("账户配置",lambda:self.navigate(1),nav=True)
        for b in (self.work_nav,self.account_nav):b.setCheckable(True);nav_group.addWidget(b)
        header.addWidget(nav)
        right=QHBoxLayout();right.addStretch();self.health=label("● 未配置下载凭据",False);self.health.setObjectName("health");right.addWidget(self.health);header.addLayout(right,1)
        layout.addWidget(top)
        self.pages=QStackedWidget();layout.addWidget(self.pages,1);self.build_workbench();self.build_accounts()
        self.debounce=QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(350);self.debounce.timeout.connect(self.calculate)
        self.ledger_timer=QTimer(self);self.ledger_timer.timeout.connect(self.refresh_quota);self.ledger_timer.start(15000)
        self.update_health();self.navigate(0);self.apply_theme();QApplication.styleHints().colorSchemeChanged.connect(lambda _:self.apply_theme())
        self.web.load(QUrl(self.server.url+"map.html"))
        if self.config_error:self.map_status.setText(self.config_error)

    def apply_theme(self):
        dark=QApplication.styleHints().colorScheme()==Qt.ColorScheme.Dark
        self.theme_dark=dark;c=dict(THEMES["dark" if dark else "light"])
        c["arrow"]=resource('assets/chevron-dark.svg' if dark else 'assets/chevron-light.svg').as_posix()
        palette=QPalette()
        for role,color in ((QPalette.ColorRole.Window,c["bg"]),(QPalette.ColorRole.WindowText,c["ink"]),(QPalette.ColorRole.Base,c["field"]),(QPalette.ColorRole.AlternateBase,c["sunken"]),(QPalette.ColorRole.Text,c["ink"]),(QPalette.ColorRole.Button,c["surface"]),(QPalette.ColorRole.ButtonText,c["ink"]),(QPalette.ColorRole.Highlight,c["accent"]),(QPalette.ColorRole.HighlightedText,c["on_accent"]),(QPalette.ColorRole.PlaceholderText,c["muted"])):palette.setColor(role,QColor(color))
        for role in (QPalette.ColorRole.Text,QPalette.ColorRole.ButtonText,QPalette.ColorRole.WindowText):palette.setColor(QPalette.ColorGroup.Disabled,role,QColor(c["muted"]))
        QApplication.setPalette(palette)
        self.setStyleSheet(STYLE.substitute(c))
        for view in (self.web,self.test_view):view.page().runJavaScript('window.setTheme&&setTheme('+json.dumps(dark)+')')

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        for w,on in ((self.work_nav,index==0),(self.account_nav,index==1)):w.setChecked(on)
        if index==1:self.refresh_quota()

    def build_workbench(self):
        work=QWidget();outer=QVBoxLayout(work);outer.setContentsMargins(0,0,0,0);split=QSplitter(Qt.Orientation.Horizontal);split.setHandleWidth(8);split.setChildrenCollapsible(False)
        self.side_scroll=scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(360);scroll.setMaximumWidth(430);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);scroll.setFrameShape(QFrame.Shape.NoFrame)
        pane=QWidget();side=QVBoxLayout(pane);side.setContentsMargins(0,0,8,2);side.setSpacing(12)
        # 任务：名称与输出位置
        group,body=card("任务");form=form_layout();body.addLayout(form)
        self.name=QLineEdit("地图_"+datetime.now().strftime("%m%d_%H%M"));form_row(form,"任务名称",self.name)
        download_dir=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        self.output=QLineEdit(str((Path(download_dir) if download_dir else Path.home()/"Downloads")/"Mapdln"));self.output.setToolTip("任务成果保存在 输出位置 / 任务名称")
        outrow=QHBoxLayout();outrow.setSpacing(6);outrow.addWidget(self.output,1);browse=button("浏览",self.choose_output);outrow.addWidget(browse);form_row(form,"输出位置",outrow)
        side.addWidget(group)
        # 地图与输出参数
        group,body=card("地图与输出","视图缩放、瓦片层级与输出分辨率相互独立；1 米像素不等于 1 米定位精度。");form=form_layout();body.addLayout(form)
        self.export_content=combo([("影像 + 高程",True),("仅高程",False)]);form_row(form,"导出内容",self.export_content)
        self.imagery_source=combo([(s['name'],s['id']) for s in list_sources(self.settings)]);self.imagery_source.setCurrentIndex(max(0,self.imagery_source.findData(self.settings.get('imagery_source','mapbox'))));form_row(form,"导出影像",self.imagery_source)
        self.preview=combo([("跟随导出影像","follow"),("高德 · 国内推荐","amap"),("百度 · 国内预览","baidu"),("Mapbox · 全球卫星","mapbox"),("离线坐标预览","offline")]);self.preview.setCurrentIndex(max(0,self.preview.findData(self.settings.get("preview_provider","amap"))));form_row(form,"预览底图",self.preview)
        self.elevation=combo([("纯黑平坦 · 0 米","flat"),("真实地形 · FABDEM","real"),("本地 DEM","local")]);form_row(form,"高程来源",self.elevation)
        self.dem_panel=QWidget();dp=QVBoxLayout(self.dem_panel);dp.setContentsMargins(0,0,0,0);dp.setSpacing(6)
        self.dem_path=DemDrop();self.dem_path.fileDropped.connect(self.set_dem_file);dp.addWidget(self.dem_path)
        dr=QHBoxLayout();dr.addWidget(button("选择文件",self.choose_dem));dr.addWidget(button("定位到数据",self.locate_dem));dp.addLayout(dr)
        self.dem_unit=combo([("读取高程单位","auto"),("高程单位：米","m"),("高程单位：英尺","ft")]);dp.addWidget(self.dem_unit)
        self.dem_status=label("选择带地理参考的单波段 .tif / .tiff",muted=True);dp.addWidget(self.dem_status);form.addWidget(self.dem_panel,form._rows,0,1,2);form._rows+=1;self.dem_panel.hide()
        self.resolution=number(.01,1000,1,2);self.resolution.setSuffix(" m/px");form_row(form,"输出分辨率",self.resolution)
        presets=QHBoxLayout();presets.setSpacing(6);self.resolution_chips={}
        def sync_chips(value):
            for n,chip in self.resolution_chips.items():chip.setChecked(abs(value-n)<1e-9)
        for n in (.5,1,2,5):
            chip=button(f"{n:g} m",lambda _,v=n:(self.resolution.setValue(v),sync_chips(v)),chip=True);chip.setCheckable(True);self.resolution_chips[n]=chip;presets.addWidget(chip)
        self.resolution.valueChanged.connect(sync_chips);sync_chips(self.resolution.value())
        form_row(form,"",presets);self.zoom=integer(0,24,17);self.auto_zoom=QCheckBox("按输出分辨率自动选择");self.auto_zoom.setToolTip('关闭后可在高级设置中调整手动瓦片层级');self.auto_zoom.setChecked(True);form_row(form,"影像清晰度",self.auto_zoom);self.zoom.setEnabled(False)
        body.addWidget(button("管理图源 / 配置密钥",self.manage_sources))
        side.addWidget(group)
        # 额度预判
        budgetgroup,bf=card("下载前额度预判","按实际选区与瓦片相交计算，并扣除有效缓存")
        self.estimate_stats=QWidget();sg=QGridLayout(self.estimate_stats);sg.setContentsMargins(0,0,0,0);sg.setSpacing(8);self.stat_values={}
        for i,(key,caption) in enumerate((("tiles","所需瓦片"),("cache","有效缓存"),("new","新增请求"),("reserve","含重试预留"),("network","预计网络"),("size","输出像素"))):
            tile=QFrame();tile.setObjectName("stat");tv=QVBoxLayout(tile);tv.setContentsMargins(10,7,10,8);tv.setSpacing(1)
            value=label("—",False,"stat");tv.addWidget(value);tv.addWidget(label(caption,False,muted=True));sg.addWidget(tile,i//2,i%2);self.stat_values[key]=value
        sg.setColumnStretch(0,1);sg.setColumnStretch(1,1);bf.addWidget(self.estimate_stats)
        self.estimate_label=label("绘制选区或输入 WGS84 中心坐标后预判额度",muted=True);bf.addWidget(self.estimate_label)
        bf.addWidget(divider())
        self.balance_label=label("账户剩余额度未知");bf.addWidget(self.balance_label)
        self.preview_usage=label("高德 / 百度预览不产生 Mapbox 请求",muted=True);bf.addWidget(self.preview_usage)
        limits=form_layout();self.reserve=integer(0,100,10);self.reserve.setSuffix(" %");form_row(limits,"重试预留",self.reserve)
        self.budget=integer(0,1000000,0);self.budget.setSpecialValueText("不限 · 仅本机预算");form_row(limits,"任务请求预算",self.budget);bf.addLayout(limits)
        bf.addWidget(button("重新检查缓存 / 额度",self.replan))
        side.addWidget(budgetgroup)
        # 高级设置
        self.advanced_toggle=button("▸  "+ADVANCED_TITLE,lambda:None);self.advanced_toggle.setObjectName("disclosure");self.advanced_toggle.setCheckable(True);side.addWidget(self.advanced_toggle)
        self.advanced=QWidget();av=QVBoxLayout(self.advanced);av.setContentsMargins(0,0,0,0);av.setSpacing(12)
        self.coordinate_card,gf=card("WGS84 坐标 / 米制尺寸","直接输入中心点创建矩形或圆形选区，并定位地图");coords=QGridLayout();coords.setHorizontalSpacing(12);coords.setVerticalSpacing(10);coords.setColumnStretch(0,1);coords.setColumnStretch(1,1)
        self.lon=number(-180,180,121.58901,7);self.lat=number(-80,84,31.2592,7)
        self.input_shape=combo([("矩形","rectangle"),("圆形","circle")]);self.width_m=number(.1,100000,200,1);self.height_m=number(.1,100000,200,1);self.radius_m=number(.1,50000,100,1)
        for i,(title,field) in enumerate((("经度",self.lon),("纬度",self.lat),("形状",self.input_shape),("圆形半径 · 米",self.radius_m),("矩形宽 · 米",self.width_m),("矩形高 · 米",self.height_m))):grid_field(coords,i//2,i%2,title,field)
        gf.addLayout(coords);gf.addWidget(button("应用选区 / 定位",self.apply_coordinates));av.addWidget(self.coordinate_card)
        def shape_fields():
            circle=self.input_shape.currentData()=='circle';self.radius_m.setEnabled(circle);self.width_m.setEnabled(not circle);self.height_m.setEnabled(not circle)
        self.input_shape.currentIndexChanged.connect(shape_fields);shape_fields()
        network,nf=card("下载与网络");self.proxy=QLineEdit();self.proxy.setPlaceholderText("HTTP / SOCKS 代理 URL · 仅本次会话");nf.addWidget(self.proxy)
        row=QHBoxLayout();row.addWidget(button("应用代理",self.apply_proxy));row.addWidget(button("网络测试",self.test_network));nf.addLayout(row)
        opts=QGridLayout();opts.setHorizontalSpacing(12);opts.setVerticalSpacing(10);opts.setColumnStretch(0,1);opts.setColumnStretch(1,1)
        self.concurrency=integer(1,8,4);self.retries=integer(0,10,3);self.connect_timeout=integer(1,120,10);self.read_timeout=integer(1,300,30);self.bits=combo([("16 位",16),("8 位",8)]);self.smooth=number(0,5,0,1)
        for i,(title,field) in enumerate((("并发",self.concurrency),("重试",self.retries),("连接超时 · 秒",self.connect_timeout),("读取超时 · 秒",self.read_timeout),("高度位深",self.bits),("平滑 σ",self.smooth))):grid_field(opts,i//2,i%2,title,field)
        grid_field(opts,3,0,'手动影像瓦片层级',self.zoom)
        nf.addLayout(opts);av.addWidget(network)
        terminal,lf=card("终端日志","凭据与代理已遮蔽");self.logs=QPlainTextEdit();self.logs.setObjectName("terminal");self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(4000);self.logs.setFixedHeight(190);lf.addWidget(self.logs)
        lr=QHBoxLayout();lr.addWidget(button("复制日志",lambda:QApplication.clipboard().setText(self.logs.toPlainText())));lr.addWidget(button("保存日志",self.save_log));lf.addLayout(lr);av.addWidget(terminal)
        side.addWidget(self.advanced);self.advanced.hide();self.advanced_toggle.toggled.connect(self.show_advanced);side.addStretch();scroll.setWidget(pane);split.addWidget(scroll)
        # 中央：地图卡片 + 底部任务栏
        center=QWidget();cl=QVBoxLayout(center);cl.setContentsMargins(0,0,0,0);cl.setSpacing(12)
        mapcard=QFrame();mapcard.setObjectName("mapCard");ml=QVBoxLayout(mapcard);ml.setContentsMargins(1,1,1,1);ml.setSpacing(0)
        bar=QFrame();bar.setObjectName("mapBar");bl=QHBoxLayout(bar);bl.setContentsMargins(15,9,15,9);bl.setSpacing(12)
        bl.addWidget(label("地图预览",False,"title"));self.map_status=label("地图预览与卫星下载来源独立",muted=True);bl.addWidget(self.map_status,1);ml.addWidget(bar)
        self.web=self.make_web(False);ml.addWidget(self.web,1)
        foot=QFrame();foot.setObjectName("mapBar");fl=QHBoxLayout(foot);fl.setContentsMargins(15,8,15,9)
        self.area_label=label("尚未选择区域。支持矩形、圆形、多边形和闭合插值曲线。",role="info");fl.addWidget(self.area_label,1);ml.addWidget(foot)
        cl.addWidget(mapcard,1)
        self.task_card,tl=card();tl.setContentsMargins(16,12,16,14);tl.setSpacing(8)
        head=QHBoxLayout();head.setSpacing(12);head.addWidget(label("任务进度",False,"title"),0,Qt.AlignmentFlag.AlignTop)
        self.stage=label("待开始 · 在地图上绘制选区",role="stage");head.addWidget(self.stage,1);tl.addLayout(head)
        row=QHBoxLayout();row.setSpacing(8)
        self.progress=QProgressBar();self.progress.setValue(0);row.addWidget(self.progress,1);row.addSpacing(6)
        self.pause=button("暂停",lambda:self.command("pause"));self.resume=button("继续",lambda:self.command("resume"));self.cancel=button("取消",lambda:self.command("cancel"),danger=True)
        for b in (self.pause,self.resume,self.cancel):b.setEnabled(False);row.addWidget(b)
        self.flat_retry=button("改用 0 米继续导出",self.retry_flat);self.flat_retry.hide();row.addWidget(self.flat_retry)
        row.addSpacing(6);row.addWidget(button("输出目录",self.open_output))
        self.start=button("开始下载",lambda:self.start_task(),True,large=True);self.start.setEnabled(False);row.addWidget(self.start)
        tl.addLayout(row);cl.addWidget(self.task_card)
        split.addWidget(center);split.setStretchFactor(0,0);split.setStretchFactor(1,1);split.setSizes([400,1100]);outer.addWidget(split,1)
        self.pages.addWidget(work)
        self.preview.currentIndexChanged.connect(self.preview_changed)
        self.imagery_source.currentIndexChanged.connect(self.source_changed)
        self.export_content.currentIndexChanged.connect(self.source_changed)
        self.elevation.currentIndexChanged.connect(self.elevation_changed)
        self.dem_unit.currentIndexChanged.connect(lambda:self.set_dem_file(self.dem_path.text()) if self.dem_path.text() else None)
        self.auto_zoom.toggled.connect(lambda on:(self.zoom.setEnabled(not on and bool(self.export_content.currentData())),self.replan()))
        for w in (self.resolution,self.zoom,self.reserve):w.valueChanged.connect(self.replan)

    def show_advanced(self,on):
        self.advanced.setVisible(on);self.advanced_toggle.setText(("▾  " if on else "▸  ")+ADVANCED_TITLE)
        if on:QTimer.singleShot(0,lambda:self.side_scroll.ensureWidgetVisible(self.coordinate_card))

    def selected_source(self):
        return get_source(self.settings,self.imagery_source.currentData())

    def preview_provider(self):
        if not self.export_content.currentData():return 'offline'
        return 'source:'+self.imagery_source.currentData() if self.preview.currentData()=='follow' else self.preview.currentData()

    def persist_preferences(self):
        self.settings.update(preview_provider=getattr(self,'preview_before_dem',self.preview.currentData()),imagery_source=self.imagery_source.currentData())
        try:save({k:v for k,v in self.settings.items() if k!='proxy'})
        except Exception as exc:self.append_log('保存偏好失败：'+str(exc))

    def preview_changed(self):
        self.persist_preferences();self.reload_map();self.refresh_quota()

    def source_changed(self):
        imagery=bool(self.export_content.currentData());self.imagery_source.setEnabled(imagery);self.auto_zoom.setEnabled(imagery);self.zoom.setEnabled(imagery and not self.auto_zoom.isChecked())
        if not imagery and not hasattr(self,'preview_before_dem'):
            self.preview_before_dem=self.preview.currentData();self.preview.blockSignals(True);self.preview.setCurrentIndex(self.preview.findData('offline'));self.preview.blockSignals(False)
            self.server.set_preview_enabled(False);self.reload_map()
        elif imagery and hasattr(self,'preview_before_dem'):
            self.preview.blockSignals(True);self.preview.setCurrentIndex(max(0,self.preview.findData(self.preview_before_dem)));self.preview.blockSignals(False);del self.preview_before_dem
            self.server.set_preview_enabled(True);self.reload_map()
        self.preview.setEnabled(imagery)
        self.start.setText('开始下载' if imagery else '开始导出')
        self.persist_preferences();self.replan();self.update_health();self.refresh_quota()
        if self.preview.currentData()=='follow':self.reload_map()

    def elevation_changed(self):
        self.dem_panel.setVisible(self.elevation.currentData()=='local');self.render_dem();self.replan()

    def manage_sources(self):
        from .source_dialog import SourcesDialog
        dialog=SourcesDialog(self.settings,self.options(),self)
        if dialog.exec():
            old=getattr(dialog,'saved_id',self.imagery_source.currentData());self.settings=dialog.settings
            self.imagery_source.blockSignals(True);self.imagery_source.clear()
            for source in list_sources(self.settings):self.imagery_source.addItem(source['name'],source['id'])
            self.imagery_source.setCurrentIndex(max(0,self.imagery_source.findData(old)));self.imagery_source.blockSignals(False)
            for key,field in self.fields.items():field.setText(self.settings.get(key,''))
            self.source_changed()

    def choose_dem(self):
        path,_=QFileDialog.getOpenFileName(self,'选择本地 DEM',self.dem_path.text(),'GeoTIFF (*.tif *.tiff)')
        if path:self.set_dem_file(path)

    def set_dem_file(self,path):
        if not path or self.selection_locked:return
        self.dem_path.setText(path);self.dem_path.setToolTip(path);self.dem_info=None;self.dem_generation+=1;generation=self.dem_generation;self.render_dem()
        self.dem_status.setText('正在读取坐标、覆盖范围和高程单位…');self.elevation.setCurrentIndex(self.elevation.findData('local'));self.replan()
        unit=self.dem_unit.currentData()
        def complete(info,error):
            if generation!=self.dem_generation:return
            if error:self.dem_status.setText('文件不可用：'+str(error));return
            self.dem_info=info
            res=info.get('resolution_m',[]);resolution=' × '.join(f'{v:.2f}' for v in res) if res else '未知'
            self.dem_status.setText(f"{info['width']:,} × {info['height']:,} 像素 · 原始约 {resolution} 米\n{info['crs']}\n"+('请选择高程单位（文件未声明）' if info.get('unit_required') else '高程单位：'+str(info.get('unit')))+'；重采样不会增加地形细节')
            self.render_dem();self.replan()
        self.jobs.run(lambda:inspect_dem(path,unit),complete)

    def render_dem(self):
        points=[]
        if self.dem_info and self.elevation.currentData()=='local':
            w,s,e,n=self.dem_info['bounds_wgs84'];points=[from_wgs(p,self.display_system) for p in ((w,s),(e,s),(e,n),(w,n),(w,s))]
        if hasattr(self,'web'):self.web.page().runJavaScript('window.setDemCoverage&&setDemCoverage('+json.dumps(points)+')')

    def locate_dem(self):
        if self.dem_info:
            w,s,e,n=self.dem_info['bounds_wgs84'];points=[from_wgs(p,self.display_system) for p in ((w,s),(e,n))]
            self.web.page().runJavaScript('window.fitCoverage&&fitCoverage('+json.dumps(points)+')')

    def show_estimate(self,result=None,message=""):
        values={}
        if result:
            values={"tiles":f"{result['tiles']:,}","cache":f"{result['cache']:,}","new":f"{result['new_requests']:,}","reserve":f"{result['with_retry_reserve']:,}",
                    "network":f"{result['estimated_download_mb']:.1f} MB","size":f"{result['width']:,} × {result['height']:,}"}
            message=f"预计临时磁盘 {result['estimated_disk_mb']:.0f} MB · 分块处理内存约 {result['estimated_working_memory_mb']} MB；均为估算"
        for key,value in self.stat_values.items():value.setText(values.get(key,"—"))
        self.estimate_label.setText(message)

    def make_web(self,test):
        view=QWebEngineView();p=QWebEngineProfile(view);page=Page(p,view);view.setPage(page)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,False)
        channel=QWebChannel(page);bridge=Bridge(self,test);channel.registerObject("bridge",bridge);page.setWebChannel(channel)
        page.serviceAlert.connect(lambda message:bridge.mapStatus(message,False))
        view._bridge=bridge;view._channel=channel;view._profile=p;return view

    def build_accounts(self):
        page=QScrollArea();page.setWidgetResizable(True);page.setFrameShape(QFrame.Shape.NoFrame);page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);content=QWidget();layout=QVBoxLayout(content);layout.setContentsMargins(4,4,12,4);layout.setSpacing(14)
        intro=QVBoxLayout();intro.setSpacing(4);intro.addWidget(label("账户配置",False,"pageTitle"))
        intro.addWidget(label("按所选图源配置凭据；仅高程导出无需影像凭据。凭据由当前 Windows 用户加密保存，任务使用启动时的配置。",muted=True));intro.addWidget(button("天地图与自定义图源",self.manage_sources));layout.addLayout(intro)
        self.account_cards={};self.account_grid=cards=QGridLayout();cards.setHorizontalSpacing(14);cards.setVerticalSpacing(14);cards.setColumnStretch(0,1);cards.setColumnStretch(1,1);layout.addLayout(cards)
        for i,(platform,(title,fields,console,docs,guide)) in enumerate(GUIDES.items()):
            card_frame,body=card(title,CAPTIONS[platform]);self.account_cards[platform]=card_frame;form=QGridLayout();form.setHorizontalSpacing(14);form.setVerticalSpacing(10);form.setColumnStretch(0,1);form.setColumnStretch(1,1)
            for key,title,is_secret in fields:
                field=QLineEdit(self.settings.get(key,""));field.setMinimumWidth(0);field.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);self.fields[key]=field;editor=field
                if is_secret:
                    field.setEchoMode(QLineEdit.EchoMode.Password);editor=QWidget();row=QHBoxLayout(editor);row.setContentsMargins(0,0,0,0);row.setSpacing(8);row.addWidget(field,1);show=QCheckBox("显示");show.toggled.connect(lambda on,w=field:w.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password));row.addWidget(show)
                index=fields.index((key,title,is_secret));grid_field(form,index//2,index%2,title,editor)
                field.textChanged.connect(lambda _,p=platform:self.dirty(p))
            if len(fields)==1:form.addWidget(label("仅用于地图预览\n无需填写服务端 SK / SN",muted=True),0,1,Qt.AlignmentFlag.AlignBottom)
            body.addLayout(form);state=label("已保存" if self.settings.get(fields[-1][0]) else "尚未配置",muted=True);self.card_status[platform]=state;body.addWidget(state)
            actions=QGridLayout();actions.setHorizontalSpacing(8);actions.setVerticalSpacing(8)
            for col in range(3):actions.setColumnStretch(col,1)
            actions.addWidget(button("保存配置",lambda _,p=platform:self.save_platform(p),True),0,0);actions.addWidget(button("测试 · 1 次请求" if platform=="mapbox" else "测试地图",lambda _,p=platform:self.test_platform(p)),0,1);actions.addWidget(button("删除配置",lambda _,p=platform:self.delete_platform(p),danger=True),0,2)
            guidebox=label(guide+"\n\n资料核对：2026-10-07；申请和额度规则以当前控制台为准。",role="callout");guidebox.hide()
            actions.addWidget(button("获取 Token" if platform=="mapbox" else "获取 Key / AK",lambda _,w=guidebox:w.setVisible(not w.isVisible())),1,0);actions.addWidget(button("平台控制台",lambda _,u=console:QDesktopServices.openUrl(QUrl(u))),1,1);actions.addWidget(button("官方教程",lambda _,u=docs:QDesktopServices.openUrl(QUrl(u))),1,2);body.addLayout(actions)
            if platform!="mapbox":
                body.addWidget(divider());body.addWidget(label("本机预览地址 · 用于域名 / Referer 白名单",False,muted=True))
                address=QLineEdit(self.server.url+"map.html");address.setReadOnly(True);address.setToolTip(self.server.url+"map.html");address.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);ar=QHBoxLayout();ar.setSpacing(8);ar.addWidget(address,1);ar.addWidget(button("复制地址",lambda:QApplication.clipboard().setText(self.server.url+"map.html")));body.addLayout(ar)
            body.addWidget(guidebox);body.addStretch();cards.addWidget(card_frame,i//2,i%2)
        quota,qf=card("Mapbox 额度与本机记录","手动录入控制台数据作为基准");self.account_cards['quota']=quota;qgrid=QGridLayout();qgrid.setHorizontalSpacing(14);qgrid.setVerticalSpacing(10);qgrid.setColumnStretch(0,1);qgrid.setColumnStretch(1,1)
        self.account_type=combo([("未知 / 未核对","unknown"),("标准账户","standard"),("试用账户","trial")]);self.account_type.setCurrentIndex(max(0,self.account_type.findData(self.settings.get("account_type","unknown"))))
        self.quota=integer(0,1000000000,int(self.settings.get("quota",0)));self.quota.setSpecialValueText("未知");self.usage=integer(0,1000000000,int(self.settings.get("usage",0)));self.cycle=QLineEdit(self.settings.get("cycle",""));self.cycle.setPlaceholderText("周期 / 控制台来源");self.cycle.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        for i,(title,field) in enumerate((("账户类型",self.account_type),("本周期请求额度",self.quota),("控制台已用基准",self.usage),("周期 / 数据来源",self.cycle))):grid_field(qgrid,i//2,i%2,title,field)
        qf.addLayout(qgrid);self.quota_status=label("",muted=True);qf.addWidget(self.quota_status);qf.addWidget(button("保存手动基准 / 更新时间",self.save_quota,True))
        qf.addWidget(label("未录入额度时显示未知。本机记录不代表全账户计费；公共 Token 不自动查询余额，具体额度以控制台为准。",role="callout"));qf.addStretch();cards.addWidget(quota,1,1)
        terrain,tb=card("FABDEM 真实地形","无需 API Key");tf=QHBoxLayout();tf.setSpacing(8)
        tf.addWidget(label("原始分辨率约 30 米。0 米模式跳过地形网络；失败后可保留卫星成果再切换 0 米。",muted=True),1);tf.addWidget(button("数据来源 / 许可",lambda:QDesktopServices.openUrl(QUrl("https://research-information.bris.ac.uk/en/datasets/fabdem-v1-2/"))));tf.addWidget(button("网络测试",self.test_terrain));tb.addLayout(tf);layout.addWidget(terrain)
        tester,vb=card("地图加载测试");self.test_status=label("地图测试由您主动触发；高德 / 百度测试不消耗 Mapbox 下载请求。",muted=True);vb.addWidget(self.test_status)
        self.test_view=self.make_web(True);self.test_view.setMinimumHeight(300);self.test_view.hide();vb.addWidget(self.test_view);layout.addWidget(tester)
        layout.addStretch();page.setWidget(content);self.pages.addWidget(page)

    def dirty(self,platform):
        fields=GUIDES[platform][1]
        if platform in self.card_status:
            changed=any(self.fields[k].text()!=self.settings.get(k,"") for k,_,_ in fields)
            self.card_status[platform].setText("有未保存编辑" if changed else "已保存" if self.settings.get(fields[-1][0]) else "尚未配置")
    def save_platform(self,platform):
        data={k:v for k,v in self.settings.items() if k!="proxy"}
        for key,_,_ in GUIDES[platform][1]:data[key]=self.fields[key].text().strip()
        if platform=="mapbox" and data.get("mapbox_token") and not data["mapbox_token"].startswith("pk."):self.card_status[platform].setText("请输入 pk. 公共 Token");return
        try:
            save(data);data["proxy"]=self.proxy.text().strip();self.settings=data;self.dirty(platform);self.update_health()
            if self.preview_provider() in (platform,'source:'+platform):self.reload_map()
        except Exception as exc:self.card_status[platform].setText("保存失败："+str(exc))
    def delete_platform(self,platform):
        data={k:v for k,v in self.settings.items() if k!="proxy"}
        for key,_,_ in GUIDES[platform][1]:data.pop(key,None)
        try:
            save(data);data["proxy"]=self.proxy.text().strip();self.settings=data
            for key,_,_ in GUIDES[platform][1]:self.fields[key].clear()
            self.dirty(platform);self.update_health()
            if self.preview_provider() in (platform,'source:'+platform):self.reload_map()
        except Exception as exc:self.card_status[platform].setText("删除失败："+str(exc))
    def update_health(self):
        ok=True;message='● 仅高程导出'
        if self.export_content.currentData():
            source=self.selected_source();credential=credentials_for(self.settings,source['id'])
            try:
                import mercantile
                build_request(source,mercantile.Tile(0,0,source['min_zoom']),credential)
                message='● '+source['name']
            except Exception:ok=False;message='● 请配置所选图源凭据'
        self.health.setText(message);self.health.setProperty("state","ok" if ok else "warn");repolish(self.health)
        if self.plan:self.start.setEnabled(not self.running())
    def test_platform(self,platform):
        if any(self.fields[k].text().strip()!=self.settings.get(k,"") for k,_,_ in GUIDES[platform][1]):self.card_status[platform].setText("请先保存编辑，再测试已保存配置");return
        if platform=="mapbox":
            token=self.settings.get("mapbox_token","")
            if not token:self.card_status[platform].setText("请先保存公共 Token");return
            self.card_status[platform].setText("正在验证瓦片接口；发起 1 次请求，重试关闭");options=self.options();options["retries"]=0
            def test():
                import mercantile
                return fetch_tile(mercantile.tile(121.58901,31.2592,17),token,options,Control(lambda *a,**k:None),Ledger(),"verify",True)
            def complete(result,error):
                self.card_status[platform].setText("连接成功；验证瓦片可按缓存规则复用" if not error else redact(error,[token]));self.refresh_quota();self.replan()
            self.jobs.run(test,complete)
        else:
            if not self.settings.get("amap_key" if platform=="amap" else "baidu_ak"):self.card_status[platform].setText("请先保存此平台配置");return
            self.test_provider=platform;self.test_success=False;self.test_view.show();self.test_view.load(QUrl(self.server.url+"map.html"));self.test_status.setText("实际加载 "+GUIDES[platform][0]+"；使用该平台额度")
    def save_quota(self):
        data={k:v for k,v in self.settings.items() if k!="proxy"};data.update(account_type=self.account_type.currentData(),quota=self.quota.value(),usage=self.usage.value(),cycle=self.cycle.text(),quota_updated=datetime.now(timezone.utc).isoformat())
        try:save(data);data["proxy"]=self.proxy.text().strip();self.settings=data;self.refresh_quota()
        except Exception as exc:self.quota_status.setText("保存失败："+str(exc))
    def refresh_quota(self):
        if not hasattr(self,"quota_status"):return
        counts=Ledger().counts(source='mapbox');since=Ledger().counts(self.settings.get("quota_updated",""),source='mapbox');recorded=sum(since.get(k,0) for k in ("download","preview","verify"));q=int(self.settings.get("quota",0));used=int(self.settings.get("usage",0));update=self.settings.get("quota_updated","尚未更新")
        selected=Ledger().counts(source=self.imagery_source.currentData());self.preview_usage.setText(f"{self.imagery_source.currentText()} · 本机下载 {selected['download']} / 预览 {selected['preview']} / 验证 {selected['verify']} 次")
        self.quota_status.setText(f"本机：下载 {counts['download']} / 预览 {counts['preview']} / 验证 {counts['verify']}；其中重试 {counts['retry']}\n手动更新：{update}；周期：{self.settings.get('cycle','未填写')}")
        if not self.export_content.currentData():self.balance_label.setText('仅高程：不请求影像瓦片')
        elif self.imagery_source.currentData()!='mapbox':self.balance_label.setText('所选平台剩余额度未知；请以平台控制台为准')
        elif not q:self.balance_label.setText("Mapbox 剩余额度未知；可在账户页手动更新")
        else:
            remaining=max(0,q-used-recorded);required=self.plan.get("with_retry_reserve",0) if self.plan else 0
            self.balance_label.setText(f"参考剩余 {remaining:,} 次\n"+("预计超出已录入额度，请核对控制台" if required>remaining else "预估在已录入额度内")+"\n非实时全账户余额，其他设备用量未知")
    def apply_coordinates(self):
        if self.selection_locked:return
        selection={"shape":self.input_shape.currentData(),"system":"WGS84","points":[[self.lon.value(),self.lat.value()]]}
        if selection["shape"]=="rectangle":selection.update(width_m=self.width_m.value(),height_m=self.height_m.value())
        else:selection["radius_m"]=self.radius_m.value()
        try:
            self.grid=make_grid(selection,self.resolution.value());self.selection=selection;self.render_selection();self.web.page().runJavaScript("setCenter("+json.dumps(from_wgs(selection["points"][0],self.display_system))+")");self.replan()
        except Exception as exc:self.map_status.setText(str(exc))
    def render_selection(self):
        if not self.grid:return
        from pyproj import Transformer
        inv=Transformer.from_crs(self.grid.crs,4326,always_xy=True);controls=self.grid.controls
        if self.selection["shape"]=="rectangle":
            corners=list(self.grid.polygon.exterior.coords)
            controls=[inv.transform(*corners[3]),inv.transform(*corners[1])]
        elif self.selection["shape"]=="circle":
            x0,y0,x1,y1=self.grid.polygon.bounds;controls=[inv.transform((x0+x1)/2,(y0+y1)/2),inv.transform(x1,(y0+y1)/2)]
        data={"shape":self.selection["shape"],"controls":[from_wgs(p,self.display_system) for p in controls],"boundary":[from_wgs(p,self.display_system) for p in self.grid.geographic.exterior.coords]}
        if self.selection['shape']=='rectangle':
            import math
            center=self.grid.polygon.centroid
            data['rectangle']={'center':from_wgs(inv.transform(center.x,center.y),self.display_system),
                               'width_m':math.dist(corners[0],corners[3]),'height_m':math.dist(corners[0],corners[1]),
                               'angle_deg':float(self.selection.get('angle_deg',0))%360}
        if self.selection['shape']=='circle':data['radius_m']=(self.grid.polygon.bounds[2]-self.grid.polygon.bounds[0])/2
        self.web.page().runJavaScript("updateSelection("+json.dumps(data)+")");self.area_label.setText(f"面积 {self.grid.polygon.area/10000:.3f} 公顷 · {self.grid.width:,} × {self.grid.height:,} 像素 · {self.grid.crs.to_string()} · {self.grid.resolution:g} m/px")
    def reload_map(self,*args):
        if hasattr(self,"web"):self.web.load(QUrl(self.server.url+"map.html"))
    def replan(self,*args):
        if not hasattr(self,"debounce") or self.selection_locked:return
        self.plan=None;self.start.setEnabled(False);self.plan_generation+=1
        if self.selection:self.debounce.start()
    def calculate(self):
        if not self.selection:return
        selection=json.loads(json.dumps(self.selection));res=self.resolution.value();zoom=None if self.auto_zoom.isChecked() else self.zoom.value();reserve=self.reserve.value();generation=self.plan_generation;self.estimate_label.setText("正在检查网格、瓦片相交与有效缓存…")
        source=self.selected_source();credentials=credentials_for(self.settings,source['id']);imagery=bool(self.export_content.currentData())
        if self.elevation.currentData()=='local':
            if not self.dem_info:self.show_estimate(None,'请先选择有效的本地 DEM');return
            try:snapshot_dem(self.dem_path.text(),self.dem_unit.currentData())
            except Exception as exc:self.show_estimate(None,str(exc));return
        def complete(result,error):
            if generation!=self.plan_generation:return
            if error:self.show_estimate(None,str(error));return
            self.plan=result;self.grid=make_grid(selection,res);self.render_selection()
            self.show_estimate(result)
            if imagery and result.get('zoom') is not None:
                self.zoom.blockSignals(True);self.zoom.setValue(result['zoom']);self.zoom.blockSignals(False)
                self.estimate_label.setText(self.estimate_label.text()+f"\n{source['name']} · 层级 {result['zoom']} · 原始像素约 {result.get('native_resolution_m',0):.2f} 米")
            self.start.setEnabled(not self.running());self.refresh_quota()
        self.jobs.run(lambda:estimate(selection,res,zoom,reserve,source=source,imagery=imagery,credentials=credentials),complete)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,"选择输出位置",self.output.text())
        if path:self.output.setText(path)
    def options(self):
        return {"proxy":self.proxy.text().strip(),"connect_timeout":self.connect_timeout.value(),"read_timeout":self.read_timeout.value(),"concurrency":self.concurrency.value(),"retries":self.retries.value(),"bit_depth":self.bits.currentData(),"smooth":self.smooth.value()}
    def running(self):
        return self.process is not None and self.process.state()!=QProcess.ProcessState.NotRunning
    def set_selection_locked(self,locked):
        self.selection_locked=bool(locked);self.coordinate_card.setEnabled(not locked)
        for widget in (self.export_content,self.imagery_source,self.elevation,self.dem_panel,self.resolution,self.auto_zoom,self.zoom):widget.setEnabled(not locked)
        for chip in self.resolution_chips.values():chip.setEnabled(not locked)
        if not locked:
            self.imagery_source.setEnabled(bool(self.export_content.currentData()));self.zoom.setEnabled(bool(self.export_content.currentData()) and not self.auto_zoom.isChecked())
        self.preview.setEnabled(bool(self.export_content.currentData()))
        self.web.page().runJavaScript("setSelectionLocked("+json.dumps(self.selection_locked)+")")
        if locked:
            self.plan_generation+=1;self.debounce.stop()
    def start_task(self,override=None):
        if self.running():return
        if override is None:
            if not self.plan or not self.selection:return
            name=self.name.text().strip()
            if not name or any(c in name for c in '<>:"/\\|?*') or name.endswith((" ",".")) or name in (".",".."):self.stage.setText("任务名包含 Windows 路径特殊字符");return
            task={"selection":json.loads(json.dumps(self.selection)),"resolution":self.resolution.value(),"zoom":None if self.auto_zoom.isChecked() else self.zoom.value(),"elevation":self.elevation.currentData(),"output":str(Path(self.output.text())/name),"task_id":uuid.uuid4().hex,"budget":self.budget.value(),"options":self.options(),"source":self.selected_source(),"imagery":bool(self.export_content.currentData())}
            credentials=credentials_for(self.settings,task['source']['id']) if task['imagery'] else {}
            try:
                if task['elevation']=='local':task['dem']=snapshot_dem(self.dem_path.text(),self.dem_unit.currentData())
                if task['imagery']:
                    import mercantile
                    build_request(task['source'],mercantile.tile(self.grid.geographic.centroid.x,self.grid.geographic.centroid.y,self.plan['zoom']),credentials)
            except Exception as exc:self.stage.setText(str(exc));return
            self.active_credentials=json.loads(json.dumps(credentials))
        else:
            task=override
            self.selection=json.loads(json.dumps(task['selection']));self.grid=make_grid(self.selection,task['resolution']);self.render_selection()
        self.active_task=task;self.terrain_failed=False;self.flat_retry.hide();self.buffer=b"";self.task_state="running";self.set_selection_locked(True);self.logs.appendPlainText("启动独立后台任务；凭据通过 stdin 传递，任务固定使用启动时的图源与参数。")
        self.process=QProcess(self);self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels);self.process.readyReadStandardOutput.connect(self.read_worker);self.process.readyReadStandardError.connect(self.read_errors);self.process.finished.connect(self.worker_finished);self.process.errorOccurred.connect(self.worker_error)
        if getattr(sys,"frozen",False):program=sys.executable;args=["--worker"]
        else:program=sys.executable;args=[str(Path(__file__).resolve().parent.parent/"main.py"),"--worker"]
        payload=(json.dumps({"task":task,"token":self.active_credentials},ensure_ascii=False)+"\n").encode("utf-8")
        self.process.started.connect(lambda:self.process.write(payload));self.process.start(program,args);self.start.setEnabled(False);self.pause.setEnabled(True);self.cancel.setEnabled(True);self.resume.setEnabled(False);self.stage.setText("启动后台任务…")
    def read_worker(self):
        self.buffer+=bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line,self.buffer=self.buffer.split(b"\n",1)
            try:event=json.loads(line)
            except (ValueError,UnicodeDecodeError):self.append_log(line.decode("utf-8",errors="replace"));continue
            self.handle_event(event)
    def handle_event(self,event):
        kind=event.get("type")
        if kind=="progress":
            total=event.get("total",0);completed=event.get("completed",0);self.progress.setRange(0,100 if total else 0)
            if total:self.progress.setValue(round(100*completed/total))
            suffix=f" · {completed:,}/{total:,}" if total else " · 处理中"
            if event.get("speed"):suffix+=f" · {event['speed']} 张/s"
            self.stage.setText(event.get("stage","")+suffix)
        elif kind=="state":
            state=event.get("state","");self.task_state=state;self.stage.setText(event.get("message",state));self.pause.setEnabled(state=="running");self.resume.setEnabled(state in ("paused","budget"))
        elif kind=="terrain_failed":self.terrain_failed=True;self.append_log(event.get("message",""))
        elif kind=="error":self.task_state="failed";self.stage.setText(event["message"]);self.append_log(event["message"])
        elif kind=="done":self.task_state="done";self.last_output=event["output"];self.stage.setText("完成 · 所选影像 / 高程 / 掩膜成果已导出");self.stage.setToolTip(event["output"]);self.progress.setRange(0,100);self.progress.setValue(100);self.append_log("输出目录："+event["output"])
        elif kind=="log":self.append_log(event.get("message",""))
    def worker_finished(self,code,status):
        self.read_worker();self.read_errors()
        if code!=0 and self.task_state not in ("failed","cancelled"):self.task_state="failed";self.stage.setText(f"任务异常退出（{code}），请查看日志")
        for b in (self.pause,self.resume,self.cancel):b.setEnabled(False)
        self.set_selection_locked(False);self.start.setEnabled(bool(self.plan));self.flat_retry.setVisible(self.terrain_failed);self.refresh_quota();self.replan()
    def worker_error(self,error):
        self.append_log("后台进程错误："+self.process.errorString())
        if self.process.state()==QProcess.ProcessState.NotRunning:
            self.set_selection_locked(False);self.stage.setText("无法启动后台任务，请查看日志");self.start.setEnabled(bool(self.plan))
            for b in (self.pause,self.resume,self.cancel):b.setEnabled(False)
    def read_errors(self):
        text=bytes(self.process.readAllStandardError()).decode("utf-8",errors="replace").strip()
        if text:self.append_log(text)
    def append_log(self,text):
        self.logs.appendPlainText(redact(text,secret_values({k:self.settings.get(k) for k in SECRETS})+secret_values(getattr(self,'active_credentials',{}))+[self.proxy.text().strip()]))
    def command(self,action):
        if self.running():
            command={"action":action}
            if action=="resume":command["budget"]=self.budget.value()
            self.process.write((json.dumps(command)+"\n").encode())
            if action=="cancel":self.stage.setText("取消中，等待当前请求或处理块结束…")
    def retry_flat(self):
        if self.running():return
        task=json.loads(json.dumps(self.active_task));task["elevation"]="flat";task["task_id"]=uuid.uuid4().hex;task["budget"]=self.budget.value();self.start_task(task)
    def open_output(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(getattr(self,"last_output",str(Path(self.output.text())/self.name.text()))))
    def save_log(self):
        path,_=QFileDialog.getSaveFileName(self,"保存已遮蔽日志","mapdln.log","日志 (*.log)")
        if path:Path(path).write_text(self.logs.toPlainText(),encoding="utf-8")
    def apply_proxy(self):
        from urllib.parse import urlsplit
        value=self.proxy.text().strip()
        try:
            if value:
                u=urlsplit(value)
                if u.scheme not in ("http","socks5","socks5h") or not u.hostname or not u.port:raise ValueError("请填 http://主机:端口 或 socks5://主机:端口")
                p=QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy if u.scheme=="http" else QNetworkProxy.ProxyType.Socks5Proxy,u.hostname,u.port,u.username or "",u.password or "")
            else:p=QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
            QNetworkProxy.setApplicationProxy(p);self.settings["proxy"]=value;self.reload_map();self.append_log("Qt 预览代理已应用；Python 下载使用启动时代理。请分别验证地图实际加载和下载网络。")
        except Exception as exc:self.append_log(str(exc))
    def test_network(self):
        options=self.options()
        def work():
            with session_for(options) as session:
                r=session.get("https://api.mapbox.com/",timeout=(options["connect_timeout"],options["read_timeout"]));return f"Python 网络可达，Mapbox 主机 HTTP {r.status_code}（无 Token，非瓦片验证）"
        self.jobs.run(work,lambda result,error:self.append_log(result if not error else "Python 下载网络失败："+redact(error)))
    def test_terrain(self):
        options=self.options()
        def work():
            with session_for(options) as session:
                r=session.head(FABDEM_BASE+"FABDEM_v1-2_tiles.geojson",timeout=(options["connect_timeout"],options["read_timeout"]));r.raise_for_status();return "FABDEM 网络可达（HEAD），实际下载还需支持 Range"
        self.jobs.run(work,lambda result,error:self.test_status.setText(result if not error else "FABDEM 网络失败："+redact(error)))
    def closeEvent(self,event):
        if self.running():
            self.command("cancel");self.process.waitForFinished(1500)
            if self.running():self.process.kill();self.process.waitForFinished(1000)
        self.jobs.pool.shutdown(wait=False,cancel_futures=True);self.server.close();event.accept()


def main():
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Mapdln.Desktop')
    app=QApplication(sys.argv);app.setApplicationName("Mapdln");app.setApplicationVersion(__version__);app.setWindowIcon(QIcon(str(resource('assets/app.ico'))));app.setFont(QFont("Microsoft YaHei",10));window=Window();window.show();return app.exec()
