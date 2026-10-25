"""Native Qt mouse/scroll checks against the actual, offline map editor.

An isolated profile prevents account changes and all external tile requests.
"""
import json
import time
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mapdln.gui import Window


def wait_for(predicate,timeout=8000):
    end=time.monotonic()+timeout/1000
    while time.monotonic()<end:
        if predicate():return
        QTest.qWait(25)
    raise AssertionError('Qt interaction timed out')


def js(w,expression):
    result=[]
    w.web.page().runJavaScript('JSON.stringify(('+expression+'))',lambda value:result.append(value))
    wait_for(lambda:result)
    return json.loads(result[0]) if result[0] else None


def target(w):return w.web.focusProxy() or w.web


def click(w,x,y):
    QTest.mouseClick(target(w),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(round(x),round(y)))
    QTest.qWait(70)


def move(w,x,y,buttons=Qt.MouseButton.NoButton,modifiers=Qt.KeyboardModifier.NoModifier):
    # QWidget QTest.mouseMove uses the system cursor on Windows, which can
    # lose hover events when focus changes. Deliver native Qt input directly.
    widget=target(w);p=QPoint(round(x),round(y))
    event=QMouseEvent(QEvent.Type.MouseMove,QPointF(p),QPointF(widget.mapToGlobal(p)),Qt.MouseButton.NoButton,buttons,modifiers)
    QApplication.sendEvent(widget,event)
    QTest.qWait(70)


def drag(w,start,end,button=Qt.MouseButton.LeftButton,modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mousePress(target(w),button,modifiers,QPoint(round(start[0]),round(start[1])))
    QTest.qWait(50)
    for f in (.25,.5,.75,1):
        move(w,start[0]+(end[0]-start[0])*f,start[1]+(end[1]-start[1])*f,button,modifiers)
    QTest.mouseRelease(target(w),button,modifiers,QPoint(round(end[0]),round(end[1])))
    QTest.qWait(180)


def wheel(w,x,y,delta=120):
    widget=target(w);p=QPoint(x,y);global_p=widget.mapToGlobal(p)
    event=QWheelEvent(QPointF(p),QPointF(global_p),QPoint(),QPoint(0,delta),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QApplication.sendEvent(widget,event);QTest.qWait(600)


def tool(w,name):js(w,f"document.querySelector('[data-tool={name}]').click()")


def rectangle(w):
    tool(w,'rectangle');drag(w,(240,230),(440,380))
    try:wait_for(lambda:w.selection is not None and w.selection['shape']=='rectangle')
    except AssertionError:
        raise AssertionError({'map':js(w,'mapDebugInfo()'),'status':w.map_status.text(),'selection':w.selection,'draft':js(w,"document.getElementById('draft').getAttribute('d')")})
    wait_for(lambda:js(w,"!!document.querySelector('.rotate-handle')"))


@pytest.fixture(scope='module')
def editor(tmp_path_factory):
    from unittest.mock import patch
    profile=tmp_path_factory.mktemp('map-ui')
    with patch.dict('os.environ',{'MAPDLN_HOME':str(profile)}):
        app=QApplication.instance() or QApplication([])
        w=Window();w.show();w.raise_();w.activateWindow();w.web.setFocus()
        wait_for(lambda:js(w,"!!window.mapDebugInfo&&mapDebugInfo().provider==='offline'&&mapDebugInfo().hasMap"))
        yield w
        w.close();app.processEvents()


@pytest.fixture(autouse=True)
def reset(editor):
    editor.set_selection_locked(False)
    js(editor,"document.getElementById('clear').click()")
    tool(editor,'pan')
    wait_for(lambda:editor.selection is None)


@pytest.mark.parametrize('kind',['polygon','curve'])
def test_hover_previews_and_only_clicked_points_are_committed(editor,kind):
    w=editor;tool(w,kind)
    click(w,240,230);click(w,420,230)
    move(w,450,370)
    a=js(w,"document.getElementById('draft').getAttribute('d')")
    assert a and a.endswith('Z') and 'NaN' not in a
    assert w.selection is None
    move(w,390,400)
    b=js(w,"document.getElementById('draft').getAttribute('d')")
    assert a!=b
    if kind=='curve':assert b.count('L')>8
    click(w,450,370)
    move(w,210,400)
    js(w,"document.getElementById('finish').click()")
    wait_for(lambda:w.selection is not None)
    assert w.selection['shape']==kind and len(w.selection['points'])==3
    assert js(w,"document.getElementById('draft').getAttribute('d')") == ''


def test_rectangle_rotation_resize_and_move_preserve_orientation(editor):
    w=editor;rectangle(w)
    before=w.grid.polygon.area
    h=js(w,"(()=>{const e=document.querySelector('.rotate-handle');return [+e.getAttribute('cx'),+e.getAttribute('cy')]})()")
    r=js(w,"(()=>{const b=document.getElementById('outline').getBBox();return [b.x+b.width/2,b.y+b.height/2]})()")
    v=(h[0]-r[0],h[1]-r[1])
    drag(w,h,(r[0]-v[1],r[1]+v[0]),modifiers=Qt.KeyboardModifier.ShiftModifier)
    wait_for(lambda:abs(w.selection.get('angle_deg',0))>10)
    angle=w.selection['angle_deg']
    assert angle==pytest.approx(270,abs=1)
    assert w.grid.polygon.area==pytest.approx(before,rel=1e-5)
    handle=js(w,"(()=>{const e=document.querySelector('.handle');return [+e.getAttribute('cx'),+e.getAttribute('cy')]})()")
    drag(w,handle,(handle[0]-25,handle[1]+35))
    assert w.selection.get('angle_deg')==pytest.approx(angle)
    assert w.grid.polygon.area!=pytest.approx(before,rel=.01)
    before_move=w.grid.polygon.area
    box=js(w,"(()=>{const b=document.getElementById('outline').getBBox();return [b.x+b.width/2,b.y+b.height/2]})()")
    drag(w,box,(box[0]+35,box[1]+20))
    assert w.selection.get('angle_deg')==pytest.approx(angle)
    assert w.grid.polygon.area==pytest.approx(before_move,rel=1e-5)


@pytest.mark.parametrize('surface',['inside','handle','drawing','outside'])
def test_wheel_zoom_on_all_editor_surfaces(editor,surface):
    w=editor;rectangle(w);point=(320,300)
    if surface=='handle':
        point=js(w,"(()=>{const e=document.querySelector('.handle');return [Math.round(+e.getAttribute('cx')),Math.round(+e.getAttribute('cy'))]})()")
    elif surface=='drawing':tool(w,'polygon')
    elif surface=='outside':point=(550,430)
    previous=deepcopy(w.selection);zoom=js(w,'mapDebugInfo().zoom')
    wheel(w,*point)
    assert js(w,'mapDebugInfo().zoom')>zoom
    assert w.selection==previous
    # Return to the same scale for the other native interactions.
    wheel(w,*point,delta=-120)


@pytest.mark.parametrize('drawing',[False,True])
def test_middle_pan_preserves_selection_and_draft(editor,drawing):
    w=editor;rectangle(w);previous=deepcopy(w.selection)
    if drawing:
        tool(w,'curve');click(w,230,240);click(w,400,240)
    before=js(w,'mapDebugInfo().center')
    drag(w,(320,300),(375,335),Qt.MouseButton.MiddleButton)
    assert js(w,'mapDebugInfo().center')!=before
    assert w.selection==previous
    assert js(w,'mapDebugInfo().draftPoints')==(2 if drawing else 0)
    assert not js(w,"document.body.classList.contains('map-panning')")


def test_download_lock_covers_ui_bridge_coordinates_reload_and_navigation(editor):
    w=editor;rectangle(w);previous=deepcopy(w.selection)
    w.set_selection_locked(True)
    assert js(w,'mapDebugInfo().selectionLocked')
    assert not w.coordinate_card.isEnabled()
    assert js(w,"getComputedStyle(document.getElementById('outline')).fill")=='rgba(128, 136, 144, 0.333)'
    assert js(w,"getComputedStyle(document.getElementById('handles')).display")=='none'
    for name in ['rectangle','circle','polygon','curve']:tool(w,name)
    js(w,"document.getElementById('clear').click()")
    drag(w,(320,300),(365,330))
    w.apply_coordinates();w.web._bridge.clearRegion()
    w.web._bridge.selectRegion(json.dumps({'shape':'circle','points':[[121.5,31.2]],'radius_m':20}))
    assert w.selection==previous
    zoom=js(w,'mapDebugInfo().zoom');wheel(w,320,300)
    assert js(w,'mapDebugInfo().zoom')>zoom
    drag(w,(320,300),(365,330),Qt.MouseButton.MiddleButton)
    assert w.selection==previous
    w.reload_map()
    wait_for(lambda:js(w,"!!window.mapDebugInfo&&mapDebugInfo().hasMap&&mapDebugInfo().selectionLocked"))
    assert js(w,"document.body.classList.contains('selection-locked')")
    w.set_selection_locked(False)
    assert w.coordinate_card.isEnabled()
    js(w,"document.getElementById('clear').click()")
    wait_for(lambda:w.selection is None)


def test_real_cached_worker_lock_pause_resume_cancel_and_complete(editor,tmp_path):
    from PIL import Image
    from mapdln.engine import tile_path
    from mapdln.geometry import tiles_for
    w=editor
    w.width_m.setValue(90);w.height_m.setValue(60);w.apply_coordinates()
    wait_for(lambda:w.plan is not None)
    for tile in tiles_for(w.grid,w.zoom.value()):
        p=tile_path(tile);p.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(256,256),(90,145,180)).save(p,'JPEG')
    w.settings['mapbox_token']='pk.testing'
    w.output.setText(str(tmp_path));w.name.setText('cancelled')
    w.start_task();w.command('pause')
    wait_for(lambda:w.task_state=='paused')
    assert w.selection_locked and js(w,'mapDebugInfo().selectionLocked')
    previous=deepcopy(w.selection)
    w.web._bridge.clearRegion();assert w.selection==previous
    w.command('cancel')
    assert w.selection_locked
    wait_for(lambda:not w.running())
    assert w.task_state=='cancelled' and not w.selection_locked
    wait_for(lambda:w.plan is not None)
    w.name.setText('complete');w.start_task();w.command('pause')
    wait_for(lambda:w.task_state=='paused')
    assert w.selection_locked
    w.command('resume');assert w.selection_locked
    wait_for(lambda:not w.running(),timeout=15000)
    assert w.task_state=='done' and not w.selection_locked
    assert (tmp_path/'complete'/'mask.png').is_file()
    assert json.loads((tmp_path/'complete'/'metadata.json').read_text())['local_mapbox_requests']==0
