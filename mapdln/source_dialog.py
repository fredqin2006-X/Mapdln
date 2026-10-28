"""User-managed raster sources; network probes are explicit and bounded."""
import copy
import json
import uuid
import concurrent.futures
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit

from PySide6.QtCore import QObject,Signal,Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,
    QLineEdit,QPlainTextEdit,QSpinBox,QDoubleSpinBox,QPushButton,QScrollArea,
    QCheckBox,QFileDialog,QFrame,QAbstractSpinBox,QListWidget,QListWidgetItem)

from .config import save,redact,Ledger
from .sources import (list_sources,get_source,credentials_for,validate_source,split_credentials,
    parse_wmts,secret_values,build_request,BUILTIN_IDS,EDITABLE_BUILTINS,provider_note)
from .engine import session_for,fetch_tile,Control
from .geometry import from_wgs
from .widgets import FlatCombo


SOURCE_GUIDES={
 "mapbox":("获取 Token","https://console.mapbox.com/account/access-tokens/","https://docs.mapbox.com/accounts/guides/tokens/",
  "① 打开控制台，注册或登录。\n② 复制可用的 pk. 公共 Token；试用账户可用默认 Token，不要求新建。\n③ 填入 Key / Token 并保存。\n④ 测试：实际发起 1 次瓦片请求，计入本机记录。\n\n401：Token 无效/已删除。403：检查账户状态、URL 限制。桌面请求不带网页 Referer，不建议伪造 Referer。超时属于网络问题。\n\n资料核对：2026-10-07；申请和额度规则以当前控制台为准。")}


class Probe(QObject):
    completed=Signal(object,object)
    def __init__(self,parent):
        super().__init__(parent);self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    def run(self,work):
        def invoke():
            try:result,error=work(),None
            except Exception as exc:result,error=None,exc
            try:self.completed.emit(result,error)
            except RuntimeError:pass
        self.pool.submit(invoke)


def surface(title=None):
    frame=QFrame();frame.setObjectName('card');body=QVBoxLayout(frame);body.setContentsMargins(16,14,16,16);body.setSpacing(10)
    if title:
        heading=QLabel(title);heading.setProperty('role','title');body.addWidget(heading)
    return frame,body


class SourcesPanel(QWidget):
    """Source list and editor; emits ``changed`` with the saved id, or None after deletion."""
    changed=Signal(object)

    def __init__(self,settings,options,host=None,parent=None):
        super().__init__(parent);self.settings=copy.deepcopy(settings);self.options=dict(options);self.host=host
        self.current_id=None;self.draft_id='custom-'+uuid.uuid4().hex[:12];self.current_credentials={};self.wmts_specs=[];self.wmts_spec=None;self.busy=False
        self.loading=False;self.saved_id=None;self.test_center=None
        self.probe=Probe(self);self.probe.completed.connect(self.probe_complete);self.probe_callback=None
        outer=QHBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(14)
        # 左侧：全部图源列表
        side=QWidget();side.setFixedWidth(300);self.side_layout=QVBoxLayout(side);self.side_layout.setContentsMargins(0,0,0,0);self.side_layout.setSpacing(12)
        listcard,lb=surface('影像图源');lb.setContentsMargins(10,14,10,12)
        hint=QLabel('选择图源查看并配置凭据；工作台的“导出影像”从这里的图源中选择。');hint.setWordWrap(True);hint.setProperty('muted',True);hint.setContentsMargins(6,0,6,0);lb.addWidget(hint)
        self.source_list=QListWidget();self.source_list.setObjectName('sourceList');self.source_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);lb.addWidget(self.source_list,1)
        row=QHBoxLayout();row.setSpacing(8);self.new_button=QPushButton('添加图源');self.new_button.clicked.connect(self.new_source);self.import_button=QPushButton('导入配置');self.import_button.clicked.connect(self.import_source)
        row.addWidget(self.new_button);row.addWidget(self.import_button);lb.addLayout(row)
        self.side_layout.addWidget(listcard,1);outer.addWidget(side)
        # 右侧：当前图源编辑
        editor,eb=surface();eb.setSpacing(12)
        head=QHBoxLayout();head.setSpacing(10);self.heading=QLabel();self.heading.setProperty('role','pageTitle');self.heading.setStyleSheet('font-size:18px');head.addWidget(self.heading)
        self.kind=QLabel();self.kind.setProperty('role','version');head.addWidget(self.kind,0,Qt.AlignmentFlag.AlignVCenter);head.addStretch();eb.addLayout(head)
        self.note=QLabel();self.note.setWordWrap(True);self.note.setProperty('muted',True);eb.addWidget(self.note)
        self.guide_row=QWidget();gr=QHBoxLayout(self.guide_row);gr.setContentsMargins(0,0,0,0);gr.setSpacing(8)
        self.guide_button=QPushButton();self.console_button=QPushButton('平台控制台');self.docs_button=QPushButton('官方教程')
        for b in (self.guide_button,self.console_button,self.docs_button):gr.addWidget(b)
        gr.addStretch();eb.addWidget(self.guide_row)
        self.guide=QLabel();self.guide.setWordWrap(True);self.guide.setProperty('role','callout');self.guide.hide();eb.addWidget(self.guide)
        self.guide_button.clicked.connect(lambda:self.guide.setVisible(not self.guide.isVisible()))
        self.console_button.clicked.connect(lambda:self.open_guide(1));self.docs_button.clicked.connect(lambda:self.open_guide(2))
        line=QFrame();line.setObjectName('divider');line.setFixedHeight(1);eb.addWidget(line)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);eb.addWidget(scroll,1)
        page=QWidget();self.content_page=page;self.form=QVBoxLayout(page);self.form.setContentsMargins(0,0,8,0);self.form.setSpacing(10);scroll.setWidget(page)
        basic=QGridLayout();basic.setColumnStretch(1,1);basic.setHorizontalSpacing(14);basic.setVerticalSpacing(8);self.form.addLayout(basic)
        self.name=QLineEdit();self.protocol=FlatCombo();self.protocol.addItem('XYZ · 常用瓦片地址','xyz');self.protocol.addItem('TMS · 反向行号','tms');self.protocol.addItem('WMTS · 读取服务图层','wmts')
        self.url=QPlainTextEdit();self.url.setObjectName('sourceEditor');self.url.setFixedHeight(66);self.url.setPlaceholderText('https://example.com/{z}/{x}/{y}.png?key={token}')
        self.token=QLineEdit();self.token.setEchoMode(QLineEdit.EchoMode.Password);self.token.setPlaceholderText('无需认证的图源可留空')
        self.token_label=QLabel('Key / Token');self.mk_label=QLabel('图层 MK');self.mk=QLineEdit();self.mk.setEchoMode(QLineEdit.EchoMode.Password);self.mk.setPlaceholderText('从吉林一号图源管理复制图层 MK')
        tokenrow=QWidget();tr=QHBoxLayout(tokenrow);tr.setContentsMargins(0,0,0,0);tr.setSpacing(8);tr.addWidget(self.token,1)
        self.show_secret=QCheckBox('显示');self.show_secret.toggled.connect(self.reveal);tr.addWidget(self.show_secret)
        for row,(label,field) in enumerate(((QLabel('图源名称'),self.name),(QLabel('服务类型'),self.protocol),(QLabel('服务地址'),self.url),(self.token_label,tokenrow),(self.mk_label,self.mk))):basic.addWidget(label,row,0,Qt.AlignmentFlag.AlignTop);basic.addWidget(field,row,1)
        self.wmts_panel=QWidget();wl=QVBoxLayout(self.wmts_panel);wl.setContentsMargins(0,0,0,0);wl.setSpacing(6)
        self.read_button=QPushButton('读取 WMTS 图层 · 1 次元数据请求');self.read_button.clicked.connect(self.read_wmts);wl.addWidget(self.read_button)
        self.layers=FlatCombo();self.layers.setMinimumContentsLength(20);wl.addWidget(self.layers);self.layers.currentIndexChanged.connect(self.select_wmts);self.form.addWidget(self.wmts_panel)
        self.advanced_toggle=QCheckBox('高级设置 · 层级、坐标与请求头');self.form.addWidget(self.advanced_toggle)
        self.advanced=QWidget();ag=QGridLayout(self.advanced);ag.setContentsMargins(0,0,0,0);ag.setHorizontalSpacing(12);ag.setVerticalSpacing(8);ag.setColumnStretch(0,1);ag.setColumnStretch(1,1)
        self.minimum=QSpinBox();self.minimum.setRange(0,24);self.maximum=QSpinBox();self.maximum.setRange(0,24);self.maximum.setValue(18)
        self.size=FlatCombo();self.size.addItem('256 × 256',256);self.size.addItem('512 × 512',512)
        self.format=FlatCombo();self.format.addItems(['auto','JPEG','PNG','WEBP'])
        self.datum=FlatCombo();self.datum.addItem('WGS84 · Web Mercator','WGS84');self.datum.addItem('CGCS2000 · Web Mercator','CGCS2000');self.datum.addItem('GCJ-02 · 导出时校正','GCJ02')
        self.subdomains=QLineEdit();self.subdomains.setPlaceholderText('如 a,b,c；对应 {s}')
        self.cache=QSpinBox();self.cache.setRange(1,31536000);self.cache.setValue(43200);self.cache.setSuffix(' 秒')
        self.attribution=QLineEdit();self.attribution.setPlaceholderText('数据供应商与署名')
        self.rate=QDoubleSpinBox();self.rate.setRange(.1,20);self.rate.setValue(4);self.rate.setSuffix(' 次/秒')
        self.concurrency=QSpinBox();self.concurrency.setRange(1,8);self.concurrency.setValue(4)
        for field in (self.minimum,self.maximum,self.cache,self.rate,self.concurrency):field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        fields=[('最小层级',self.minimum),('最大层级',self.maximum),('瓦片尺寸',self.size),('图片格式',self.format),('坐标基准',self.datum),('子域列表',self.subdomains),('缓存有效期',self.cache),('署名',self.attribution),('请求速率',self.rate),('并发上限',self.concurrency)]
        for index,(caption,widget) in enumerate(fields):
            box=QVBoxLayout();box.setSpacing(4);box.addWidget(QLabel(caption));box.addWidget(widget);ag.addLayout(box,index//2,index%2)
        self.headers=QPlainTextEdit();self.headers.setObjectName('sourceEditor');self.headers.setFixedHeight(64);self.headers.setPlaceholderText('可选请求头，JSON 对象；所有值均加密保存')
        ag.addWidget(QLabel('请求头（敏感内容）'),5,0,1,2);ag.addWidget(self.headers,6,0,1,2)
        self.form.addWidget(self.advanced);self.advanced.hide();self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.form.addStretch(1)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setMinimumHeight(38);eb.addWidget(self.status)
        actions=QHBoxLayout();actions.setSpacing(8);self.export_button=QPushButton('导出配置');self.delete_button=QPushButton('删除图源');self.delete_button.setProperty('danger',True);self.test_button=QPushButton('测试 · 1 张瓦片');self.save_button=QPushButton('保存配置');self.save_button.setProperty('primary',True)
        for b in (self.export_button,self.delete_button):actions.addWidget(b)
        actions.addStretch();actions.addWidget(self.test_button);actions.addWidget(self.save_button);eb.addLayout(actions)
        for b in (self.new_button,self.import_button,self.export_button,self.delete_button,self.test_button,self.save_button,self.read_button,self.guide_button,self.console_button,self.docs_button):b.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(editor,1)
        self.export_button.clicked.connect(self.export_source);self.delete_button.clicked.connect(self.delete_source);self.test_button.clicked.connect(self.test_source);self.save_button.clicked.connect(self.save_source)
        self.source_list.currentRowChanged.connect(lambda _:self.load_source());self.protocol.currentIndexChanged.connect(self.protocol_changed)
        self.url.textChanged.connect(self.url_changed)
        self.populate('mapbox')

    # ---- 列表与同步 ----
    def source_state(self,source):
        try:
            import mercantile
            credentials=credentials_for(self.settings,source['id'])
            build_request(source,mercantile.Tile(0,0,source['min_zoom']),credentials)
            return '凭据已配置' if credentials.get('token') or credentials.get('headers') else '可用 · 无需凭据'
        except Exception:return '未配置凭据'

    def populate(self,select=None,load=True):
        self.source_list.blockSignals(True);self.source_list.clear()
        for source in list_sources(self.settings):
            item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,source['id']);item.setToolTip(source['name'])
            row=QWidget();rl=QVBoxLayout(row);rl.setContentsMargins(10,7,10,7);rl.setSpacing(1)
            title=QLabel(source['name']);title.setStyleSheet('font-weight:600;background:transparent');rl.addWidget(title)
            state=self.source_state(source);caption=QLabel(('内置' if source['id'] in BUILTIN_IDS else '自定义')+' · '+state);caption.setProperty('muted',True)
            if state=='未配置凭据':caption.setProperty('pending',True)
            rl.addWidget(caption)
            item.setSizeHint(row.sizeHint());self.source_list.addItem(item);self.source_list.setItemWidget(item,row)
        self.source_list.blockSignals(False)
        if load:self.select_source(select if select in self.ids() else self.ids()[0])
        else:
            self.source_list.blockSignals(True);self.source_list.setCurrentRow(self.ids().index(select) if select in self.ids() else -1);self.source_list.blockSignals(False)

    def ids(self):
        return [self.source_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.source_list.count())]

    def selected_id(self):
        item=self.source_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_source(self,source_id):
        if source_id not in self.ids():return
        row=self.ids().index(source_id)
        if self.source_list.currentRow()==row:self.load_source()
        else:self.source_list.setCurrentRow(row)

    def clear_selection(self):
        self.source_list.blockSignals(True);self.source_list.setCurrentRow(-1);self.source_list.clearSelection();self.source_list.blockSignals(False)

    def sync(self,settings,options):
        """Adopt the window's latest settings without discarding the form being edited."""
        self.settings=copy.deepcopy(settings);self.options=dict(options)
        if not self.busy:self.populate(self.current_id,load=False)

    def center(self):
        if self.test_center is not None:return self.test_center
        host=self.host
        if host is not None and getattr(host,'grid',None):return (host.grid.geographic.centroid.x,host.grid.geographic.centroid.y)
        if host is not None and hasattr(host,'lon'):return (host.lon.value(),host.lat.value())
        return (0.,0.)

    def reveal(self,on):
        mode=QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        self.token.setEchoMode(mode);self.mk.setEchoMode(mode)

    def open_guide(self,index):
        guide=SOURCE_GUIDES.get(self.current_id)
        if guide:QDesktopServices.openUrl(QUrl(guide[index]))

    # ---- 表单 ----
    def protocol_changed(self):
        self.wmts_panel.setVisible(self.protocol.currentData()=='wmts' and (self.current_id not in BUILTIN_IDS or self.current_id in EDITABLE_BUILTINS))

    def new_source(self):
        self.clear_selection()
        self.current_id=None;self.draft_id='custom-'+uuid.uuid4().hex[:12];self.current_credentials={};self.wmts_spec=None;self.wmts_specs=[];self.layers.clear()
        self.load_fields({'name':'自定义影像','protocol':'xyz','url_template':'','min_zoom':0,'max_zoom':18,'tile_size':256,'format':'auto','datum':'WGS84','cache_seconds':43200})
        self.status.setText('填写服务地址后，可先测试再保存。')

    def load_source(self):
        source_id=self.selected_id()
        if source_id is None:return
        self.current_id=source_id;source=get_source(self.settings,self.current_id)
        self.current_credentials=credentials_for(self.settings,self.current_id);self.wmts_spec=source if source['protocol']=='wmts' else None
        self.wmts_specs=[];self.layers.blockSignals(True);self.layers.clear();self.layers.blockSignals(False)
        self.load_fields(source);self.status.setText('')

    def load_fields(self,spec):
        self.loading=True
        builtin=self.current_id in BUILTIN_IDS;editable=not builtin or self.current_id in EDITABLE_BUILTINS
        name=get_source(self.settings,self.current_id)['name'] if builtin else spec.get('name','')
        self.name.setText(name);self.protocol.setCurrentIndex(max(0,self.protocol.findData(spec.get('protocol','xyz'))));self.url.setPlainText(spec.get('url_template',''))
        self.token.setText(self.current_credentials.get('token',''));self.minimum.setValue(spec.get('min_zoom',0));self.maximum.setValue(spec.get('max_zoom',18));self.size.setCurrentIndex(max(0,self.size.findData(spec.get('tile_size',256))));self.format.setCurrentText(spec.get('format','auto'));self.datum.setCurrentIndex(max(0,self.datum.findData(spec.get('datum','WGS84'))));self.subdomains.setText(','.join(spec.get('subdomains',[])));self.cache.setValue(spec.get('cache_seconds',43200));self.attribution.setText(spec.get('attribution',''));self.rate.setValue(spec.get('requests_per_second',4));self.concurrency.setValue(spec.get('max_concurrency',4));self.headers.setPlainText(json.dumps(self.current_credentials.get('headers',{}),ensure_ascii=False) if self.current_credentials.get('headers') else '')
        jilin=self.current_id=='jilin';self.mk.setText(self.current_credentials.get('credential_1',''));self.mk.setVisible(jilin);self.mk_label.setVisible(jilin)
        self.token_label.setText('访问 TK' if jilin else 'Key / Token');self.token.setPlaceholderText('从吉林一号图源管理复制访问 TK' if jilin else '无需认证的图源可留空')
        self.show_secret.setChecked(False)
        self.heading.setText(name or '新建自定义图源');self.kind.setText('内置' if builtin else '自定义' if self.current_id else '未保存')
        self.note.setText(provider_note(self.current_id))
        guide=SOURCE_GUIDES.get(self.current_id);self.guide_row.setVisible(bool(guide));self.guide.hide()
        if guide:self.guide_button.setText(guide[0]);self.guide.setText(guide[3])
        self.name.setEnabled(not builtin)
        for field in (self.protocol,self.url,self.advanced):field.setEnabled(editable)
        self.delete_button.setEnabled(not builtin and self.current_id is not None);self.protocol_changed()
        self.loading=False

    def url_changed(self):
        if not self.loading and self.protocol.currentData()=='wmts':
            self.wmts_spec=None;self.wmts_specs=[];self.layers.clear()

    def collect(self):
        if self.current_id in BUILTIN_IDS and self.current_id not in EDITABLE_BUILTINS:
            return get_source(self.settings,self.current_id),{'token':self.token.text().strip()}
        headers=json.loads(self.headers.toPlainText() or '{}')
        if not isinstance(headers,dict):raise ValueError('请求头必须是 JSON 对象')
        url,extracted=split_credentials(self.url.toPlainText().strip(),headers)
        credentials=copy.deepcopy(self.current_credentials);credentials.update(extracted);credentials['headers']={**headers,**extracted.get('headers',{})}
        if 'token' not in extracted:credentials['token']=self.token.text().strip()
        if self.current_id=='jilin' and 'credential_1' not in extracted:credentials['credential_1']=self.mk.text().strip()
        protocol=self.protocol.currentData()
        if protocol!='wmts' and any(marker in url for marker in ('{-y}','{reverseY}')):
            url=url.replace('{-y}','{y}').replace('{reverseY}','{y}');protocol='tms'
        spec=copy.deepcopy(self.wmts_spec or {}) if self.protocol.currentData()=='wmts' else {}
        name=get_source(self.settings,self.current_id)['name'] if self.current_id in BUILTIN_IDS else self.name.text().strip()
        spec.update(id=self.current_id or self.draft_id,name=name,protocol=protocol,url_template=url,min_zoom=self.minimum.value(),max_zoom=self.maximum.value(),tile_size=self.size.currentData(),format=self.format.currentText(),crs='EPSG:3857',datum=self.datum.currentData(),subdomains=self.subdomains.text().strip(),cache_seconds=self.cache.value(),attribution=self.attribution.text().strip(),requests_per_second=self.rate.value(),max_concurrency=self.concurrency.value())
        if spec['protocol']=='wmts' and not self.wmts_spec:raise ValueError('请先读取 WMTS 图层并选择一个兼容图层')
        return validate_source(spec),credentials

    def controls(self):
        return (self.source_list,self.new_button,self.save_button,self.test_button,self.read_button,self.import_button,self.export_button,self.delete_button)

    def start_probe(self,work,callback):
        if self.busy:return
        self.busy=True;self.probe_callback=callback
        self.content_page.setEnabled(False)
        for w in self.controls():w.setEnabled(False)
        self.probe.run(work)

    def probe_complete(self,result,error):
        self.busy=False
        self.content_page.setEnabled(True)
        for w in self.controls():w.setEnabled(True)
        self.delete_button.setEnabled(self.current_id is not None and self.current_id not in BUILTIN_IDS)
        if error:self.status.setText(redact(str(error),secret_values(self.current_credentials)+[self.token.text().strip(),self.mk.text().strip()]));return
        if self.probe_callback:self.probe_callback(result)

    def read_wmts(self):
        raw=self.url.toPlainText().strip()
        if not raw:self.status.setText('请填写 WMTS 服务地址');return
        try:
            headers=json.loads(self.headers.toPlainText() or '{}')
            if not isinstance(headers,dict):raise ValueError('请求头必须是 JSON 对象')
            safe,extracted=split_credentials(raw,headers);credentials={**self.current_credentials,**extracted};credentials['headers']={**headers,**extracted.get('headers',{})}
            if 'token' not in extracted:credentials['token']=self.token.text().strip()
            if self.current_id=='jilin' and 'credential_1' not in extracted:credentials['credential_1']=self.mk.text().strip()
            target=safe
            from urllib.parse import quote
            for name,value in credentials.items():
                if isinstance(value,str):target=target.replace('{'+name+'}',quote(value,safe=''))
            parts=urlsplit(target)
            pairs=[(k,v) for k,v in parse_qsl(parts.query) if k.lower() not in ('service','request','version','layer','style','tilematrixset','format','tilematrix','tilerow','tilecol')]+[('SERVICE','WMTS'),('REQUEST','GetCapabilities'),('VERSION','1.0.0')]
            target=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(pairs),''))
            if parts.scheme not in ('https','http') or not parts.hostname or '{' in target or '%7B' in target.upper():raise ValueError('填写有效的 HTTP(S) 服务地址和所需凭据')
            self.current_credentials=credentials;self.status.setText('正在读取服务图层；仅请求 1 次能力文档…')
            def work():
                with session_for(self.options) as session:
                    with session.get(target,headers=credentials.get('headers',{}),timeout=(10,20),stream=True,allow_redirects=False) as r:
                        r.raise_for_status()
                        if 300<=r.status_code<400:raise ValueError('服务地址发生跳转，请填写最终 WMTS 地址')
                        chunks=[];total=0
                        for chunk in r.iter_content(65536):
                            total+=len(chunk)
                            if total>4*1024*1024:raise ValueError('能力文档超过 4 MiB，请使用更具体的服务地址')
                            chunks.append(chunk)
                return parse_wmts(b''.join(chunks),safe)
            def done(specs):
                self.wmts_specs=specs;self.layers.blockSignals(True);self.layers.clear()
                for spec in specs:self.layers.addItem(spec['name'])
                self.layers.blockSignals(False);self.select_wmts();self.status.setText(f'找到 {len(specs)} 个兼容图层；选择后可测试当前图层。')
            self.start_probe(work,done)
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)))

    def select_wmts(self):
        index=self.layers.currentIndex()
        if 0<=index<len(self.wmts_specs):
            self.wmts_spec=copy.deepcopy(self.wmts_specs[index])
            if self.current_id in BUILTIN_IDS:self.wmts_spec.update(id=self.current_id,name=get_source(self.settings,self.current_id)['name'])
            self.load_fields(self.wmts_spec)

    def test_source(self):
        try:
            source,credential=self.collect();options={**self.options,'retries':0};center=self.center()
            self.current_credentials=credential;self.status.setText(f'正在验证当前位置 {center[0]:.5f}, {center[1]:.5f} 的 1 张瓦片；不重试…')
            def work():
                import mercantile
                z=min(max(12,source['min_zoom']),source['max_zoom'])
                return fetch_tile(mercantile.tile(*from_wgs(center,source['datum']),z),credential,options,Control(lambda *a,**k:None),Ledger(),'verify',True,source=source)
            self.start_probe(work,lambda result:self.status.setText('瓦片验证成功；位置与覆盖范围请在工作台预览确认。'))
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)+[self.token.text().strip(),self.mk.text().strip()]))

    def commit(self,candidate,source_id):
        save({k:v for k,v in candidate.items() if k!='proxy'});self.settings=candidate;self.saved_id=source_id
        self.populate(source_id);self.changed.emit(source_id)

    def save_source(self):
        try:
            source,credential=self.collect();candidate=copy.deepcopy(self.settings)
            if source['id']=='mapbox':
                if credential.get('token') and not credential['token'].startswith('pk.'):self.status.setText('请输入 pk. 公共 Token');return
                candidate['mapbox_token']=credential.get('token','')
            elif source['id']=='tianditu':candidate['tianditu_key']=credential.get('token','')
            elif source['id'] in EDITABLE_BUILTINS:
                candidate.setdefault('provider_sources',{})[source['id']]=source
                candidate.setdefault('source_credentials',{})[source['id']]=credential
            else:
                candidate['custom_sources']=[s for s in candidate.get('custom_sources',[]) if s['id']!=source['id']]+[source]
                candidate.setdefault('source_credentials',{})[source['id']]=credential
            self.commit(candidate,source['id']);self.status.setText('已保存并应用。')
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)+[self.token.text().strip(),self.mk.text().strip()]))

    def delete_source(self):
        if self.current_id is None or self.current_id in BUILTIN_IDS:return
        try:
            candidate=copy.deepcopy(self.settings);candidate['custom_sources']=[s for s in candidate.get('custom_sources',[]) if s['id']!=self.current_id];candidate.setdefault('source_credentials',{}).pop(self.current_id,None)
            save({k:v for k,v in candidate.items() if k!='proxy'});self.settings=candidate;self.saved_id=None
            self.populate('mapbox');self.status.setText('已删除图源。');self.changed.emit(None)
        except Exception as exc:self.status.setText(str(exc))

    def export_source(self):
        try:
            source,_=self.collect();path,_=QFileDialog.getSaveFileName(self,'导出不含凭据的图源配置',source['name']+'.json','图源配置 (*.json)')
            if path:Path(path).write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8');self.status.setText('已导出图源配置；不包含 Key、Token、MK 和请求头。')
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)))

    def import_source(self):
        path,_=QFileDialog.getOpenFileName(self,'导入图源配置','','图源配置 (*.json)')
        if not path:return
        try:
            if Path(path).stat().st_size>65536:raise ValueError('图源配置超过 64 KiB')
            spec=json.loads(Path(path).read_text(encoding='utf-8'));url,credentials=split_credentials(spec.get('url_template',''),spec.pop('headers',{}))
            if spec.get('protocol','xyz')!='wmts' and any(marker in url for marker in ('{-y}','{reverseY}')):
                url=url.replace('{-y}','{y}').replace('{reverseY}','{y}');spec['protocol']='tms'
            spec['url_template']=url;spec.pop('token',None);spec['id']='custom-'+uuid.uuid4().hex[:12];spec=validate_source(spec)
            self.clear_selection()
            self.current_id=None;self.draft_id=spec['id'];self.current_credentials=credentials;self.wmts_spec=spec if spec['protocol']=='wmts' else None;self.load_fields(spec);self.status.setText('配置已读取；检查参数并填写自己的凭据后保存。')
        except Exception as exc:self.status.setText('导入失败：'+redact(exc))

    def shutdown(self):
        self.probe.pool.shutdown(wait=False,cancel_futures=True)
