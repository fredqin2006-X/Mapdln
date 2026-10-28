"""User-managed raster sources; network probes are explicit and bounded."""
import copy
import json
import uuid
import concurrent.futures
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit

from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,
    QComboBox,QLineEdit,QPlainTextEdit,QSpinBox,QDoubleSpinBox,QPushButton,QScrollArea,
    QCheckBox,QFileDialog,QFrame,QAbstractSpinBox)

from .config import save,redact,Ledger
from .sources import (list_sources,get_source,credentials_for,validate_source,split_credentials,
    parse_wmts,secret_values,BUILTIN_IDS,EDITABLE_BUILTINS,provider_note)
from .engine import session_for,fetch_tile,Control
from .geometry import from_wgs


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


class SourcesDialog(QDialog):
    def __init__(self,settings,options,parent=None):
        super().__init__(parent);self.settings=copy.deepcopy(settings);self.options=dict(options)
        self.current_id=None;self.draft_id='custom-'+uuid.uuid4().hex[:12];self.current_credentials={};self.wmts_specs=[];self.wmts_spec=None;self.busy=False
        self.loading=False
        self.test_center=(parent.grid.geographic.centroid.x,parent.grid.geographic.centroid.y) if parent is not None and getattr(parent,'grid',None) else (parent.lon.value(),parent.lat.value()) if parent is not None and hasattr(parent,'lon') else (0.,0.)
        self.setWindowTitle('图源管理');self.resize(730,640);self.setMinimumSize(600,560)
        self.probe=Probe(self);self.probe.completed.connect(self.probe_complete);self.probe_callback=None
        outer=QVBoxLayout(self);outer.setContentsMargins(20,18,20,18);outer.setSpacing(12)
        title=QLabel('图源管理');title.setProperty('role','pageTitle');outer.addWidget(title)
        caption=QLabel('选择内置影像，或添加自己的瓦片服务。凭据加密保存在本机。');caption.setWordWrap(True);caption.setProperty('muted',True);outer.addWidget(caption)
        top=QHBoxLayout();self.selector=QComboBox();self.selector.setPlaceholderText('新建自定义图源')
        for source in list_sources(self.settings):self.selector.addItem(source['name'],source['id'])
        self.selector.setCurrentIndex(0)
        top.addWidget(self.selector,1);self.new_button=QPushButton('添加图源');self.new_button.clicked.connect(self.new_source);top.addWidget(self.new_button);outer.addLayout(top)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);outer.addWidget(scroll,1)
        page=QWidget();self.content_page=page;self.form=QVBoxLayout(page);self.form.setContentsMargins(0,0,8,0);self.form.setSpacing(10);scroll.setWidget(page)
        basic=QGridLayout();basic.setColumnStretch(1,1);basic.setHorizontalSpacing(14);basic.setVerticalSpacing(8);self.form.addLayout(basic)
        self.name=QLineEdit();self.protocol=QComboBox();self.protocol.addItem('XYZ · 常用瓦片地址','xyz');self.protocol.addItem('TMS · 反向行号','tms');self.protocol.addItem('WMTS · 读取服务图层','wmts')
        self.url=QPlainTextEdit();self.url.setObjectName('sourceEditor');self.url.setFixedHeight(66);self.url.setPlaceholderText('https://example.com/{z}/{x}/{y}.png?key={token}')
        self.token=QLineEdit();self.token.setEchoMode(QLineEdit.EchoMode.Password);self.token.setPlaceholderText('无需认证的图源可留空')
        self.token_label=QLabel('Key / Token');self.mk_label=QLabel('图层 MK');self.mk=QLineEdit();self.mk.setEchoMode(QLineEdit.EchoMode.Password);self.mk.setPlaceholderText('从吉林一号图源管理复制图层 MK')
        for row,(label,field) in enumerate(((QLabel('图源名称'),self.name),(QLabel('服务类型'),self.protocol),(QLabel('服务地址'),self.url),(self.token_label,self.token),(self.mk_label,self.mk))):basic.addWidget(label,row,0,Qt.AlignmentFlag.AlignTop);basic.addWidget(field,row,1)
        self.wmts_panel=QWidget();wl=QVBoxLayout(self.wmts_panel);wl.setContentsMargins(0,0,0,0);wl.setSpacing(6)
        self.read_button=QPushButton('读取 WMTS 图层 · 1 次元数据请求');self.read_button.clicked.connect(self.read_wmts);wl.addWidget(self.read_button)
        self.layers=QComboBox();self.layers.setMinimumContentsLength(20);wl.addWidget(self.layers);self.layers.currentIndexChanged.connect(self.select_wmts);self.form.addWidget(self.wmts_panel)
        self.advanced_toggle=QCheckBox('高级设置 · 层级、坐标与请求头');self.form.addWidget(self.advanced_toggle)
        self.advanced=QWidget();ag=QGridLayout(self.advanced);ag.setContentsMargins(0,0,0,0);ag.setHorizontalSpacing(12);ag.setVerticalSpacing(8);ag.setColumnStretch(0,1);ag.setColumnStretch(1,1)
        self.minimum=QSpinBox();self.minimum.setRange(0,24);self.maximum=QSpinBox();self.maximum.setRange(0,24);self.maximum.setValue(18)
        self.size=QComboBox();self.size.addItem('256 × 256',256);self.size.addItem('512 × 512',512)
        self.format=QComboBox();self.format.addItems(['auto','JPEG','PNG','WEBP'])
        self.datum=QComboBox();self.datum.addItem('WGS84 · Web Mercator','WGS84');self.datum.addItem('CGCS2000 · Web Mercator','CGCS2000');self.datum.addItem('GCJ-02 · 导出时校正','GCJ02')
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
        self.note=QLabel();self.note.setWordWrap(True);self.note.setProperty('muted',True);self.form.addWidget(self.note);self.form.addStretch(1)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setMinimumHeight(38);outer.addWidget(self.status)
        actions=QHBoxLayout();self.import_button=QPushButton('导入配置');self.export_button=QPushButton('导出配置');self.delete_button=QPushButton('删除');self.test_button=QPushButton('测试 · 1 张瓦片');self.save_button=QPushButton('保存并应用');self.save_button.setProperty('primary',True)
        for b in (self.import_button,self.export_button,self.delete_button):actions.addWidget(b)
        actions.addStretch();actions.addWidget(self.test_button);actions.addWidget(self.save_button);outer.addLayout(actions)
        self.import_button.clicked.connect(self.import_source);self.export_button.clicked.connect(self.export_source);self.delete_button.clicked.connect(self.delete_source);self.test_button.clicked.connect(self.test_source);self.save_button.clicked.connect(self.save_source)
        self.selector.currentIndexChanged.connect(self.load_source);self.protocol.currentIndexChanged.connect(self.protocol_changed)
        self.url.textChanged.connect(self.url_changed)
        self.load_source()

    def protocol_changed(self):
        self.wmts_panel.setVisible(self.protocol.currentData()=='wmts' and (self.current_id not in BUILTIN_IDS or self.current_id in EDITABLE_BUILTINS))

    def new_source(self):
        self.selector.blockSignals(True);self.selector.setCurrentIndex(-1);self.selector.blockSignals(False)
        self.current_id=None;self.draft_id='custom-'+uuid.uuid4().hex[:12];self.current_credentials={};self.wmts_spec=None;self.wmts_specs=[];self.layers.clear()
        self.load_fields({'name':'自定义影像','protocol':'xyz','url_template':'','min_zoom':0,'max_zoom':18,'tile_size':256,'format':'auto','datum':'WGS84','cache_seconds':43200})
        self.status.setText('填写服务地址后，可先测试再保存。')

    def load_source(self):
        self.current_id=self.selector.currentData();source=get_source(self.settings,self.current_id)
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
        self.note.setText(provider_note(self.current_id))
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

    def start_probe(self,work,callback):
        if self.busy:return
        self.busy=True;self.probe_callback=callback
        self.content_page.setEnabled(False)
        for w in (self.selector,self.new_button,self.save_button,self.test_button,self.read_button,self.import_button,self.delete_button):w.setEnabled(False)
        self.probe.run(work)

    def probe_complete(self,result,error):
        self.busy=False
        self.content_page.setEnabled(True)
        for w in (self.selector,self.new_button,self.save_button,self.test_button,self.read_button,self.import_button):w.setEnabled(True)
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
            source,credential=self.collect();options={**self.options,'retries':0}
            self.current_credentials=credential;self.status.setText(f'正在验证当前位置 {self.test_center[0]:.5f}, {self.test_center[1]:.5f} 的 1 张瓦片；不重试…')
            def work():
                import mercantile
                z=min(max(12,source['min_zoom']),source['max_zoom'])
                center=from_wgs(self.test_center,source['datum'])
                return fetch_tile(mercantile.tile(*center,z),credential,options,Control(lambda *a,**k:None),Ledger(),'verify',True,source=source)
            self.start_probe(work,lambda result:self.status.setText('瓦片验证成功；位置与覆盖范围请在工作台预览确认。'))
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)+[self.token.text().strip(),self.mk.text().strip()]))

    def save_source(self):
        try:
            source,credential=self.collect();candidate=copy.deepcopy(self.settings)
            if source['id']=='mapbox':candidate['mapbox_token']=credential.get('token','')
            elif source['id']=='tianditu':candidate['tianditu_key']=credential.get('token','')
            elif source['id'] in EDITABLE_BUILTINS:
                candidate.setdefault('provider_sources',{})[source['id']]=source
                candidate.setdefault('source_credentials',{})[source['id']]=credential
            else:
                candidate['custom_sources']=[s for s in candidate.get('custom_sources',[]) if s['id']!=source['id']]+[source]
                candidate.setdefault('source_credentials',{})[source['id']]=credential
            save({k:v for k,v in candidate.items() if k!='proxy'});self.settings=candidate;self.saved_id=source['id'];self.accept()
        except Exception as exc:self.status.setText(redact(exc,secret_values(self.current_credentials)+[self.token.text().strip(),self.mk.text().strip()]))

    def delete_source(self):
        if self.current_id is None or self.current_id in BUILTIN_IDS:return
        try:
            candidate=copy.deepcopy(self.settings);candidate['custom_sources']=[s for s in candidate.get('custom_sources',[]) if s['id']!=self.current_id];candidate.setdefault('source_credentials',{}).pop(self.current_id,None)
            save({k:v for k,v in candidate.items() if k!='proxy'});self.settings=candidate;self.accept()
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
            self.selector.blockSignals(True);self.selector.setCurrentIndex(-1);self.selector.blockSignals(False)
            self.current_id=None;self.draft_id=spec['id'];self.current_credentials=credentials;self.wmts_spec=spec if spec['protocol']=='wmts' else None;self.load_fields(spec);self.status.setText('配置已读取；检查参数并填写自己的凭据后保存。')
        except Exception as exc:self.status.setText('导入失败：'+redact(exc))

    def done(self,result):
        self.probe.pool.shutdown(wait=False,cancel_futures=True);super().done(result)
