"""Desktop workbench, accounts and responsive background process control."""
import concurrent.futures
import json
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from PySide6.QtCore import QObject,Signal,Slot,QUrl,QProcess,QTimer,Qt,QStandardPaths
from PySide6.QtGui import QDesktopServices,QFont,QIcon,QPixmap,QPalette,QColor
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox,QStackedWidget,QScrollArea,QGroupBox,
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
        w=self.window;provider=w.test_provider if self.test else w.preview.currentData()
        view=w.test_view if self.test else w.web
        view.page().runJavaScript("setTheme("+json.dumps(w.theme_dark)+");previewProvider("+json.dumps(provider)+")")
    @Slot(str)
    def providerReady(self,system):
        if not self.test:
            w=self.window;w.display_system=system;w.render_selection()
            if w.grid:
                p=w.grid.geographic.centroid
                w.web.page().runJavaScript('setCenter('+json.dumps(from_wgs([p.x,p.y],system))+')')
    @Slot(str,bool)
    def mapStatus(self,message,ok):
        w=self.window;text=redact(message,[w.settings.get(k,"") for k in SECRETS])
        if self.test:w.test_status.setText(text);w.test_success=ok
        else:w.map_status.setText(text)
    @Slot(str)
    def selectRegion(self,text):
        if self.test:return
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
        if self.test:return
        w=self.window;w.selection=None;w.grid=None;w.plan=None;w.start.setEnabled(False)
        w.plan_generation+=1
        w.debounce.stop()
        w.estimate_label.setText("绘制选区或输入 WGS84 中心坐标后预判额度")


def label(text,wrap=True):
    w=QLabel(text);w.setWordWrap(wrap);return w
def button(text,callback,primary=False):
    w=QPushButton(text);w.clicked.connect(callback)
    if primary:w.setProperty("primary",True)
    return w
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
    text=label(title,False);text.setProperty("muted",True);layout.addWidget(text);layout.addWidget(field)
    grid.addWidget(box,row,column)


def form_layout(widget):
    form=QFormLayout(widget);form.setHorizontalSpacing(12);form.setVerticalSpacing(9)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


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


class Window(QMainWindow):
    def __init__(self):
        super().__init__();QApplication.setStyle("Fusion");QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy));self.setWindowTitle("Mapdln · 可视化地图下载器 "+__version__);self.resize(1440,900);self.setMinimumSize(1080,740)
        self.setWindowIcon(QIcon(str(resource('assets/app.ico'))))
        self.settings={};self.config_error=""
        try:self.settings=load()
        except Exception as exc:self.config_error="本机凭据无法解密，请重新保存："+str(exc)
        self.selection=None;self.grid=None;self.plan=None;self.plan_generation=0;self.process=None;self.buffer=b"";self.task_state="idle"
        self.terrain_failed=False;self.display_system="WGS84";self.test_provider="amap";self.test_success=False;self.fields={};self.card_status={}
        self.jobs=Jobs(self)
        assets=resource("web")
        self.server=PreviewServer(assets,lambda:dict(self.settings))
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setContentsMargins(24,18,24,18);layout.setSpacing(14)
        header=QHBoxLayout();mark=QLabel();mark.setPixmap(QPixmap(str(resource('assets/app.png'))).scaled(40,40,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));header.addWidget(mark);title=QVBoxLayout();title.setSpacing(2);name=label("Mapdln",False);name.setObjectName("brand");title.addWidget(name);subtitle=label("选一片区域，把影像与地形带回本地",False);subtitle.setProperty("muted",True);title.addWidget(subtitle);header.addLayout(title);header.addStretch()
        self.work_nav=button("下载工作台",lambda:self.navigate(0));self.account_nav=button("账户配置",lambda:self.navigate(1));header.addWidget(self.work_nav);header.addWidget(self.account_nav)
        self.health=label("尚未配置下载凭据",False);self.health.setProperty("muted",True);header.addWidget(self.health);layout.addLayout(header)
        self.pages=QStackedWidget();layout.addWidget(self.pages,1);self.build_workbench();self.build_accounts()
        self.debounce=QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(350);self.debounce.timeout.connect(self.calculate)
        self.ledger_timer=QTimer(self);self.ledger_timer.timeout.connect(self.refresh_quota);self.ledger_timer.start(15000)
        self.update_health();self.navigate(0);self.apply_theme();QApplication.styleHints().colorSchemeChanged.connect(lambda _:self.apply_theme())
        self.web.load(QUrl(self.server.url+"map.html"))
        if self.config_error:self.map_status.setText(self.config_error)

    def apply_theme(self):
        dark=QApplication.styleHints().colorScheme()==Qt.ColorScheme.Dark
        self.theme_dark=dark
        bg,card,ink,muted,line,field,tint=("#14221f","#1d302a","#e2eeea","#a2bab2","#334a42","#243a32","#294d40") if dark else ("#f3f6f5","#ffffff","#233b34","#647b75","#e1e9e5","#f6f9f7","#e5f5ee")
        arrow=resource('assets/chevron-dark.svg' if dark else 'assets/chevron-light.svg').as_posix()
        palette=QPalette()
        for role,color in ((QPalette.ColorRole.Window,bg),(QPalette.ColorRole.WindowText,ink),(QPalette.ColorRole.Base,field),(QPalette.ColorRole.Text,ink),(QPalette.ColorRole.Button,field),(QPalette.ColorRole.ButtonText,ink),(QPalette.ColorRole.Highlight,'#07977f'),(QPalette.ColorRole.HighlightedText,'#ffffff')):palette.setColor(role,QColor(color))
        for role in (QPalette.ColorRole.Text,QPalette.ColorRole.ButtonText):palette.setColor(QPalette.ColorGroup.Disabled,role,QColor(muted))
        QApplication.setPalette(palette)
        self.setStyleSheet(f"""
        QMainWindow{{background:{bg};}}QWidget{{color:{ink};font-family:'Microsoft YaHei';font-size:13px;}}
        QLabel,QCheckBox{{background:transparent;}}
        QLabel[muted="true"]{{color:{muted};font-size:12px;}}
        QLabel#brand{{font-size:23px;font-weight:700;color:#07977f;}}
        QGroupBox{{background:{card};border:1px solid {line};border-radius:10px;margin-top:12px;padding:17px 10px 10px;}}
        QGroupBox::title{{subcontrol-origin:margin;left:14px;padding:0 5px;font-weight:600;}}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit{{background:{field};border:1px solid transparent;border-radius:6px;padding:8px 9px;selection-background-color:#07977f;selection-color:white;}}
        QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QPlainTextEdit:focus{{border:1px solid #07977f;}}
        QComboBox{{padding-right:30px;}}
        QComboBox:hover{{border:1px solid {line};}}
        QComboBox::drop-down{{subcontrol-origin:padding;subcontrol-position:top right;width:28px;border:0;background:transparent;}}
        QComboBox::down-arrow{{image:url("{arrow}");width:16px;height:16px;}}
        QComboBox QAbstractItemView{{background:{card};border:1px solid {line};border-radius:6px;padding:5px;outline:0;selection-background-color:{tint};selection-color:#07977f;}}
        QComboBox QAbstractItemView::item{{min-height:30px;padding:4px 10px;border:0;}}
        QComboBox QAbstractItemView::item:selected,QComboBox QAbstractItemView::item:hover{{background:{tint};color:#07977f;border-radius:5px;}}
        QFrame#comboPopup{{border:0;background:{card};}}
        QSpinBox:disabled,QDoubleSpinBox:disabled{{color:{muted};}}
        QPushButton{{background:{field};border:1px solid transparent;border-radius:6px;padding:8px 10px;}}
        QPushButton:hover,QPushButton:checked{{background:{tint};color:#07977f;}}QPushButton:pressed{{background:{line};}}QPushButton:disabled{{color:{muted};}}
        QPushButton[primary="true"]{{background:#07977f;color:white;font-weight:600;}}QPushButton[primary="true"]:hover{{background:#07836e;color:white;}}QPushButton[primary="true"]:disabled{{background:{line};color:{muted};}}
        QProgressBar{{border:0;border-radius:4px;background:{line};text-align:center;min-height:16px;font-size:11px;}}QProgressBar::chunk{{background:#07977f;border-radius:4px;}}
        QScrollArea{{border:0;background:transparent;}}QCheckBox{{spacing:6px;}}
        QCheckBox::indicator{{width:14px;height:14px;border:1px solid {line};border-radius:3px;background:{field};}}
        QCheckBox::indicator:checked{{background:#07977f;border-color:#07977f;}}
        QScrollBar:vertical{{border:0;background:transparent;width:6px;margin:0;}}QScrollBar::handle:vertical{{background:{line};border-radius:3px;min-height:28px;}}
        QScrollBar:horizontal{{border:0;background:transparent;height:6px;margin:0;}}QScrollBar::handle:horizontal{{background:{line};border-radius:3px;min-width:28px;}}
        QScrollBar::add-line,QScrollBar::sub-line{{border:0;width:0;height:0;}}QScrollBar::add-page,QScrollBar::sub-page{{background:transparent;}}
        QSplitter::handle{{background:transparent;width:2px;}}
        """)
        for view in (self.web,self.test_view):view.page().runJavaScript('window.setTheme&&setTheme('+json.dumps(dark)+')')

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        for w,on in ((self.work_nav,index==0),(self.account_nav,index==1)):
            w.setProperty("primary",on);w.style().unpolish(w);w.style().polish(w)
        if index==1:self.refresh_quota()

    def build_workbench(self):
        work=QWidget();outer=QVBoxLayout(work);outer.setContentsMargins(0,0,0,0);split=QSplitter(Qt.Orientation.Horizontal)
        self.side_scroll=scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(350);scroll.setMaximumWidth(410);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane=QWidget();side=QVBoxLayout(pane);side.setContentsMargins(0,0,8,4);side.setSpacing(12);group=QGroupBox("下载参数");form=form_layout(group)
        self.name=QLineEdit("地图_"+datetime.now().strftime("%m%d_%H%M"));form.addRow("任务名称",self.name)
        self.preview=combo([("高德 · 国内推荐","amap"),("百度 · 国内预览","baidu"),("Mapbox · 全球卫星","mapbox")]);self.preview.setCurrentIndex(max(0,self.preview.findData(self.settings.get("preview_provider","amap"))));form.addRow("预览地图",self.preview)
        self.elevation=combo([("纯黑平坦 · 0 米","flat"),("真实地形 · FABDEM","real")]);form.addRow("高程模式",self.elevation)
        self.resolution=number(.01,1000,1,2);self.resolution.setSuffix(" m/px");form.addRow("输出分辨率",self.resolution)
        presets=QHBoxLayout()
        for n in (.5,1,2,5):presets.addWidget(button(str(n)+" m",lambda _,v=n:self.resolution.setValue(v)))
        form.addRow("",presets);self.zoom=integer(0,22,17);form.addRow("卫星瓦片层级",self.zoom)
        download_dir=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        self.output=QLineEdit(str((Path(download_dir) if download_dir else Path.home()/"Downloads")/"Mapdln"));outrow=QHBoxLayout();outrow.addWidget(self.output);outrow.addWidget(button("…",self.choose_output));form.addRow("输出位置",outrow)
        note=label("视图缩放、瓦片层级与输出分辨率独立。1 米像素不等于 1 米定位精度。");note.setProperty("muted",True);form.addRow(note);side.addWidget(group)
        self.task_card=taskbar=QGroupBox("任务进度");tl=QVBoxLayout(taskbar);tl.setSpacing(10);self.stage=label("待开始 · 在地图上绘制选区");tl.addWidget(self.stage)
        self.progress=QProgressBar();self.progress.setValue(0);tl.addWidget(self.progress)
        controls=QHBoxLayout();self.pause=button("暂停",lambda:self.command("pause"));self.resume=button("继续",lambda:self.command("resume"));self.cancel=button("取消",lambda:self.command("cancel"))
        for b in (self.pause,self.resume,self.cancel):b.setEnabled(False);controls.addWidget(b)
        tl.addLayout(controls);self.flat_retry=button("改用 0 米继续导出",self.retry_flat);self.flat_retry.hide();tl.addWidget(self.flat_retry)
        actions=QHBoxLayout();self.start=button("开始下载",lambda:self.start_task(),True);self.start.setEnabled(False);actions.addWidget(self.start,2);actions.addWidget(button("输出目录",self.open_output),1);tl.addLayout(actions);side.addWidget(taskbar)
        budgetgroup=QGroupBox("下载前额度预判");bf=QVBoxLayout(budgetgroup);self.estimate_label=label("绘制选区或输入 WGS84 中心坐标后预判额度");bf.addWidget(self.estimate_label)
        self.balance_label=label("账户剩余额度未知");bf.addWidget(self.balance_label);bf.addWidget(button("重新检查缓存 / 额度",self.replan))
        self.preview_usage=label("高德 / 百度预览不产生 Mapbox 请求");bf.addWidget(self.preview_usage)
        self.reserve=integer(0,100,10);rr=QHBoxLayout();rr.addWidget(label("重试预留 %",False));rr.addWidget(self.reserve);bf.addLayout(rr)
        self.budget=integer(0,1000000,0);self.budget.setSpecialValueText("不限 · 仅本机预算");br=QHBoxLayout();br.addWidget(label("任务请求预算",False));br.addWidget(self.budget);bf.addLayout(br)
        side.addWidget(budgetgroup)
        self.advanced_toggle=button("高级设置 · 坐标与网络",lambda:None);self.advanced_toggle.setCheckable(True);side.addWidget(self.advanced_toggle)
        self.advanced=QWidget();av=QVBoxLayout(self.advanced);av.setContentsMargins(0,0,0,0);av.setSpacing(12)
        self.coordinate_card=geom=QGroupBox("WGS84 坐标 / 米制尺寸");gf=QVBoxLayout(geom);coords=QGridLayout();coords.setHorizontalSpacing(12);coords.setVerticalSpacing(10);coords.setColumnStretch(0,1);coords.setColumnStretch(1,1)
        self.lon=number(-180,180,121.58901,7);self.lat=number(-80,84,31.2592,7)
        self.input_shape=combo([("矩形","rectangle"),("圆形","circle")]);self.width_m=number(.1,100000,200,1);self.height_m=number(.1,100000,200,1);self.radius_m=number(.1,50000,100,1)
        for i,(title,field) in enumerate((("经度",self.lon),("纬度",self.lat),("形状",self.input_shape),("圆形半径 · 米",self.radius_m),("矩形宽 · 米",self.width_m),("矩形高 · 米",self.height_m))):grid_field(coords,i//2,i%2,title,field)
        gf.addLayout(coords);gf.addWidget(button("应用选区 / 定位",self.apply_coordinates));av.addWidget(geom)
        def shape_fields():
            circle=self.input_shape.currentData()=='circle';self.radius_m.setEnabled(circle);self.width_m.setEnabled(not circle);self.height_m.setEnabled(not circle)
        self.input_shape.currentIndexChanged.connect(shape_fields);shape_fields()
        network=QGroupBox("下载与网络");nf=QVBoxLayout(network);self.proxy=QLineEdit();self.proxy.setPlaceholderText("HTTP / SOCKS 代理 URL · 仅本次会话");nf.addWidget(self.proxy)
        row=QHBoxLayout();row.addWidget(button("应用代理",self.apply_proxy));row.addWidget(button("网络测试",self.test_network));nf.addLayout(row)
        opts=QGridLayout();opts.setHorizontalSpacing(12);opts.setVerticalSpacing(10);opts.setColumnStretch(0,1);opts.setColumnStretch(1,1)
        self.concurrency=integer(1,8,4);self.retries=integer(0,10,3);self.connect_timeout=integer(1,120,10);self.read_timeout=integer(1,300,30);self.bits=combo([("16 位",16),("8 位",8)]);self.smooth=number(0,5,0,1)
        for i,(title,field) in enumerate((("并发",self.concurrency),("重试",self.retries),("连接超时 · 秒",self.connect_timeout),("读取超时 · 秒",self.read_timeout),("高度位深",self.bits),("平滑 σ",self.smooth))):grid_field(opts,i//2,i%2,title,field)
        nf.addLayout(opts);av.addWidget(network)
        terminal=QGroupBox("终端日志");lf=QVBoxLayout(terminal);self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(4000);self.logs.setFixedHeight(170);lf.addWidget(self.logs)
        lr=QHBoxLayout();lr.addWidget(button("复制日志",lambda:QApplication.clipboard().setText(self.logs.toPlainText())));lr.addWidget(button("保存日志",self.save_log));lf.addLayout(lr);av.addWidget(terminal)
        av.insertWidget(0,button("收起高级设置",lambda:self.advanced_toggle.setChecked(False)))
        side.addWidget(self.advanced);self.advanced.hide();self.advanced_toggle.toggled.connect(self.show_advanced);side.addStretch();scroll.setWidget(pane);split.addWidget(scroll)
        center=QWidget();cl=QVBoxLayout(center);cl.setContentsMargins(8,0,0,0);cl.setSpacing(8);self.map_status=label("地图预览与卫星下载来源独立");cl.addWidget(self.map_status)
        self.web=self.make_web(False);cl.addWidget(self.web,1);self.area_label=label("尚未选择区域。支持矩形、圆形、多边形和闭合插值曲线。");cl.addWidget(self.area_label);split.addWidget(center);split.setStretchFactor(1,1);outer.addWidget(split,1)
        self.pages.addWidget(work)
        self.preview.currentIndexChanged.connect(self.reload_map)
        for w in (self.resolution,self.zoom,self.reserve):w.valueChanged.connect(self.replan)

    def show_advanced(self,on):
        self.advanced.setVisible(on)
        QTimer.singleShot(0,lambda:self.side_scroll.ensureWidgetVisible(self.coordinate_card if on else self.task_card))

    def make_web(self,test):
        view=QWebEngineView();p=QWebEngineProfile(view);page=Page(p,view);view.setPage(page)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,False)
        channel=QWebChannel(page);bridge=Bridge(self,test);channel.registerObject("bridge",bridge);page.setWebChannel(channel)
        page.serviceAlert.connect(lambda message:bridge.mapStatus(message,False))
        view._bridge=bridge;view._channel=channel;view._profile=p;return view

    def build_accounts(self):
        page=QScrollArea();page.setWidgetResizable(True);page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);content=QWidget();layout=QVBoxLayout(content);layout.setContentsMargins(0,0,8,0);layout.setSpacing(12)
        intro=label("账户配置",False);intro.setStyleSheet("font-size:21px;font-weight:600;");layout.addWidget(intro)
        hint=label("下载配置 Mapbox，预览平台按需配置。凭据使用当前 Windows 用户加密保存，任务使用启动时的配置。");hint.setProperty("muted",True);layout.addWidget(hint)
        self.account_cards={};self.account_grid=cards=QGridLayout();cards.setHorizontalSpacing(16);cards.setVerticalSpacing(14);cards.setColumnStretch(0,1);cards.setColumnStretch(1,1);layout.addLayout(cards)
        for i,(platform,(title,fields,console,docs,guide)) in enumerate(GUIDES.items()):
            card=QGroupBox(title);self.account_cards[platform]=card;body=QVBoxLayout(card);body.setSpacing(10);form=QGridLayout();form.setHorizontalSpacing(14);form.setColumnStretch(0,1);form.setColumnStretch(1,1)
            for key,title,is_secret in fields:
                field=QLineEdit(self.settings.get(key,""));field.setMinimumWidth(0);field.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);self.fields[key]=field;editor=field
                if is_secret:
                    field.setEchoMode(QLineEdit.EchoMode.Password);editor=QWidget();row=QHBoxLayout(editor);row.setContentsMargins(0,0,0,0);row.setSpacing(6);row.addWidget(field,1);show=QCheckBox("显示");show.toggled.connect(lambda on,w=field:w.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password));row.addWidget(show)
                index=fields.index((key,title,is_secret));grid_field(form,index//2,index%2,title,editor)
                field.textChanged.connect(lambda _,p=platform:self.dirty(p))
            if len(fields)==1:form.addWidget(label("仅用于地图预览\n无需填写服务端 SK / SN"),0,1)
            body.addLayout(form);state=label("已保存" if self.settings.get(fields[-1][0]) else "尚未配置");state.setProperty("muted",True);self.card_status[platform]=state;body.addWidget(state)
            actions=QGridLayout();actions.setHorizontalSpacing(8);actions.setVerticalSpacing(6)
            for col in range(3):actions.setColumnStretch(col,1)
            actions.addWidget(button("保存配置",lambda _,p=platform:self.save_platform(p),True),0,0);actions.addWidget(button("测试 · 1 次请求" if platform=="mapbox" else "测试地图",lambda _,p=platform:self.test_platform(p)),0,1);actions.addWidget(button("删除配置",lambda _,p=platform:self.delete_platform(p)),0,2)
            guidebox=label(guide+"\n\n资料核对：2026-10-07；申请和额度规则以当前控制台为准。");guidebox.setProperty("muted",True);guidebox.hide()
            actions.addWidget(button("获取 Token" if platform=="mapbox" else "获取 Key / AK",lambda _,w=guidebox:w.setVisible(not w.isVisible())),1,0);actions.addWidget(button("平台控制台",lambda _,u=console:QDesktopServices.openUrl(QUrl(u))),1,1);actions.addWidget(button("官方教程",lambda _,u=docs:QDesktopServices.openUrl(QUrl(u))),1,2);body.addLayout(actions)
            if platform!="mapbox":
                address=QLineEdit(self.server.url+"map.html");address.setReadOnly(True);address.setToolTip(self.server.url+"map.html");address.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);ar=QHBoxLayout();ar.addWidget(address,1);ar.addWidget(button("复制地址",lambda:QApplication.clipboard().setText(self.server.url+"map.html")));body.addLayout(ar)
            body.addWidget(guidebox);body.addStretch();cards.addWidget(card,i//2,i%2)
        self.account_cards['quota']=quota=QGroupBox("Mapbox 额度与本机记录");qf=QVBoxLayout(quota);qgrid=QGridLayout();qgrid.setHorizontalSpacing(14);qgrid.setColumnStretch(0,1);qgrid.setColumnStretch(1,1)
        self.account_type=combo([("未知 / 未核对","unknown"),("标准账户","standard"),("试用账户","trial")]);self.account_type.setCurrentIndex(max(0,self.account_type.findData(self.settings.get("account_type","unknown"))))
        self.quota=integer(0,1000000000,int(self.settings.get("quota",0)));self.quota.setSpecialValueText("未知");self.usage=integer(0,1000000000,int(self.settings.get("usage",0)));self.cycle=QLineEdit(self.settings.get("cycle",""));self.cycle.setPlaceholderText("周期 / 控制台来源");self.cycle.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        for i,(title,field) in enumerate((("账户类型",self.account_type),("本周期请求额度",self.quota),("控制台已用基准",self.usage),("周期 / 数据来源",self.cycle))):grid_field(qgrid,i//2,i%2,title,field)
        qf.addLayout(qgrid);self.quota_status=label("");self.quota_status.setProperty("muted",True);qf.addWidget(self.quota_status);qf.addWidget(button("保存手动基准 / 更新时间",self.save_quota));note=label("未录入额度时显示未知。本机记录不代表全账户计费；公共 Token 不自动查询余额，具体额度以控制台为准。");note.setProperty("muted",True);qf.addWidget(note);cards.addWidget(quota,1,1)
        terrain=QGroupBox("FABDEM 真实地形");tf=QHBoxLayout(terrain);note=label("无需 API Key · 原始约 30 米。0 米模式跳过地形网络；失败后可保留卫星成果再切换 0 米。");note.setProperty("muted",True);tf.addWidget(note,1);tf.addWidget(button("数据来源 / 许可",lambda:QDesktopServices.openUrl(QUrl("https://research-information.bris.ac.uk/en/datasets/fabdem-v1-2/"))));tf.addWidget(button("网络测试",self.test_terrain));layout.addWidget(terrain)
        self.test_status=label("地图测试由您主动触发；高德 / 百度测试不消耗 Mapbox 下载请求。");self.test_status.setProperty("muted",True);layout.addWidget(self.test_status);self.test_view=self.make_web(True);self.test_view.setMinimumHeight(280);self.test_view.hide();layout.addWidget(self.test_view);layout.addStretch();page.setWidget(content);self.pages.addWidget(page)

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
            if self.preview.currentData()==platform:self.reload_map()
        except Exception as exc:self.card_status[platform].setText("保存失败："+str(exc))
    def delete_platform(self,platform):
        data={k:v for k,v in self.settings.items() if k!="proxy"}
        for key,_,_ in GUIDES[platform][1]:data.pop(key,None)
        try:
            save(data);data["proxy"]=self.proxy.text().strip();self.settings=data
            for key,_,_ in GUIDES[platform][1]:self.fields[key].clear()
            self.dirty(platform);self.update_health()
            if self.preview.currentData()==platform:self.reload_map()
        except Exception as exc:self.card_status[platform].setText("删除失败："+str(exc))
    def update_health(self):
        self.health.setText("下载凭据已配置" if self.settings.get("mapbox_token") else "请配置 Mapbox Token")
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
        counts=Ledger().counts();since=Ledger().counts(self.settings.get("quota_updated",""));recorded=sum(since.get(k,0) for k in ("download","preview","verify"));q=int(self.settings.get("quota",0));used=int(self.settings.get("usage",0));update=self.settings.get("quota_updated","尚未更新")
        self.preview_usage.setText(f"本机 Mapbox 预览已发起 {counts['preview']} 次\n"+("当前为高德 / 百度，预览不产生 Mapbox 请求" if self.preview.currentData()!='mapbox' else "当前底图为 Mapbox；有效缓存可供导出复用"))
        self.quota_status.setText(f"本机：下载 {counts['download']} / 预览 {counts['preview']} / 验证 {counts['verify']}；其中重试 {counts['retry']}\n手动更新：{update}；周期：{self.settings.get('cycle','未填写')}")
        if not q:self.balance_label.setText("账户剩余额度未知；请在账户页手动更新")
        else:
            remaining=max(0,q-used-recorded);required=self.plan.get("with_retry_reserve",0) if self.plan else 0
            self.balance_label.setText(f"参考剩余 {remaining:,} 次\n"+("预计超出已录入额度，请核对控制台" if required>remaining else "预估在已录入额度内")+"\n非实时全账户余额，其他设备用量未知")
    def apply_coordinates(self):
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
            x0,y0,x1,y1=self.grid.polygon.bounds;controls=[inv.transform(x0,y0),inv.transform(x1,y1)]
        elif self.selection["shape"]=="circle":
            x0,y0,x1,y1=self.grid.polygon.bounds;controls=[inv.transform((x0+x1)/2,(y0+y1)/2),inv.transform(x1,(y0+y1)/2)]
        data={"shape":self.selection["shape"],"controls":[from_wgs(p,self.display_system) for p in controls],"boundary":[from_wgs(p,self.display_system) for p in self.grid.geographic.exterior.coords]}
        if self.selection['shape']=='circle':data['radius_m']=(self.grid.polygon.bounds[2]-self.grid.polygon.bounds[0])/2
        self.web.page().runJavaScript("updateSelection("+json.dumps(data)+")");self.area_label.setText(f"面积 {self.grid.polygon.area/10000:.3f} 公顷 · {self.grid.width:,} × {self.grid.height:,} 像素 · {self.grid.crs.to_string()} · {self.grid.resolution:g} m/px")
    def reload_map(self,*args):
        if hasattr(self,"web"):self.web.load(QUrl(self.server.url+"map.html"))
    def replan(self,*args):
        if not hasattr(self,"debounce"):return
        self.plan=None;self.start.setEnabled(False);self.plan_generation+=1
        if self.selection:self.debounce.start()
    def calculate(self):
        if not self.selection:return
        selection=json.loads(json.dumps(self.selection));res=self.resolution.value();zoom=self.zoom.value();reserve=self.reserve.value();generation=self.plan_generation;self.estimate_label.setText("正在检查网格、瓦片相交与有效缓存…")
        def complete(result,error):
            if generation!=self.plan_generation:return
            if error:self.estimate_label.setText(str(error));return
            self.plan=result;self.grid=make_grid(selection,res);self.render_selection()
            self.estimate_label.setText(f"瓦片 {result['tiles']:,} · 有效缓存 {result['cache']:,}\n新增请求 {result['new_requests']:,} · 含预留 {result['with_retry_reserve']:,}\n预计网络 {result['estimated_download_mb']:.1f} MB\n预计临时磁盘 {result['estimated_disk_mb']:.0f} MB\n输出 {result['width']:,} × {result['height']:,}\n分块处理，内存估算约 {result['estimated_working_memory_mb']} MB；均为估算")
            self.start.setEnabled(not self.running());self.refresh_quota()
        self.jobs.run(lambda:estimate(selection,res,zoom,reserve),complete)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,"选择输出位置",self.output.text())
        if path:self.output.setText(path)
    def options(self):
        return {"proxy":self.proxy.text().strip(),"connect_timeout":self.connect_timeout.value(),"read_timeout":self.read_timeout.value(),"concurrency":self.concurrency.value(),"retries":self.retries.value(),"bit_depth":self.bits.currentData(),"smooth":self.smooth.value()}
    def running(self):
        return self.process is not None and self.process.state()!=QProcess.ProcessState.NotRunning
    def start_task(self,override=None):
        if self.running():return
        if not self.settings.get("mapbox_token"):self.navigate(1);self.card_status["mapbox"].setText("开始下载需要保存公共 Token；选区和参数已保留");return
        if override is None:
            if not self.plan or not self.selection:return
            name=self.name.text().strip()
            if not name or any(c in name for c in '<>:"/\\|?*') or name.endswith((" ",".")) or name in (".",".."):self.stage.setText("任务名包含 Windows 路径特殊字符");return
            task={"selection":json.loads(json.dumps(self.selection)),"resolution":self.resolution.value(),"zoom":self.zoom.value(),"elevation":self.elevation.currentData(),"output":str(Path(self.output.text())/name),"task_id":uuid.uuid4().hex,"budget":self.budget.value(),"options":self.options()}
        else:task=override
        self.active_task=task;self.terrain_failed=False;self.flat_retry.hide();self.buffer=b"";self.task_state="running";self.logs.appendPlainText("启动独立后台任务；Token 通过 stdin 传递，代理不写入任务元数据。")
        self.process=QProcess(self);self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels);self.process.readyReadStandardOutput.connect(self.read_worker);self.process.readyReadStandardError.connect(self.read_errors);self.process.finished.connect(self.worker_finished);self.process.errorOccurred.connect(self.worker_error)
        if getattr(sys,"frozen",False):program=sys.executable;args=["--worker"]
        else:program=sys.executable;args=[str(Path(__file__).resolve().parent.parent/"main.py"),"--worker"]
        payload=(json.dumps({"task":task,"token":self.settings["mapbox_token"]},ensure_ascii=False)+"\n").encode("utf-8")
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
        elif kind=="done":self.task_state="done";self.last_output=event["output"];self.stage.setText("完成 · 卫星 / 高度 / 掩膜等成果已导出");self.stage.setToolTip(event["output"]);self.progress.setRange(0,100);self.progress.setValue(100);self.append_log("输出目录："+event["output"])
        elif kind=="log":self.append_log(event.get("message",""))
    def worker_finished(self,code,status):
        self.read_worker();self.read_errors()
        if code!=0 and self.task_state not in ("failed","cancelled"):self.task_state="failed";self.stage.setText(f"任务异常退出（{code}），请查看日志")
        for b in (self.pause,self.resume,self.cancel):b.setEnabled(False)
        self.start.setEnabled(bool(self.plan));self.flat_retry.setVisible(self.terrain_failed);self.refresh_quota();self.replan()
    def worker_error(self,error):
        self.append_log("后台进程错误："+self.process.errorString())
        if self.process.state()==QProcess.ProcessState.NotRunning:self.stage.setText("无法启动后台任务，请查看日志");self.start.setEnabled(bool(self.plan))
    def read_errors(self):
        text=bytes(self.process.readAllStandardError()).decode("utf-8",errors="replace").strip()
        if text:self.append_log(text)
    def append_log(self,text):
        self.logs.appendPlainText(redact(text,[self.settings.get(k,"") for k in SECRETS]+[self.proxy.text().strip()]))
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
