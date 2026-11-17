"""Serve a browser preview with a simulated Orca bridge. Never contacts FlashForge."""

import argparse
import http.server
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from orca_flashforge.model import demo_status  # noqa: E402
from orca_flashforge.ui_assets import render_ui  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--account", action="store_true", help="Show a simulated signed-in account and print review")
    parser.add_argument("--status-delay", type=int, default=0, help="Delay simulated status replies in milliseconds")
    args = parser.parse_args()
    status = json.dumps(demo_status())
    bridge = """<script>
    (function(){
      var callbacks=[], config=PREVIEW_CONFIG, devices=[{devId:'synthetic-device',name:'Creator · SIMULADA',model:'Creator 5 Pro',status:'ready',image_url:''}], account={nickname:'Cuenta de demostración',email:'demo@example.invalid',avatar_url:''}, profile={name:'Creator 5 Pro · DEMOSTRACIÓN',model:'Flashforge Creator 5 Pro',plate:1};
      function deliver(message){setTimeout(function(){callbacks.forEach(function(fn){fn(message);});},0);}
      window.orca={onMessage:function(fn){callbacks.push(fn);},postMessage:function(message){
        if(message.command==='refresh_status'||(message.command==='status'&&message.background)){
          setTimeout(function(){deliver({command:'status_refresh',request_id:message.request_id,device_id:message.device_id,mode:config.mode,status:DEMO_STATUS});},PREVIEW_STATUS_DELAY);
          return;
        }
        if(message.command==='config'){deliver({command:'config',status_protocol:1,config:config,devices:config.mode==='cloud'?devices:[],account:config.mode==='cloud'?account:null,profile:profile,sliced_plates:[1,2],plate_details:[{plate:1,time:'2h 35m',weight:'48.2',filaments:[{tool:0,material:'PLA simulado',color:'#57b5a5'}]},{plate:2,time:'1h 10m',weight:'18.6',filaments:[{tool:1,material:'PETG simulado',color:'#e5ac72'}]}]});}
        else if(message.command==='login'){deliver({command:'account',account:account});deliver({command:'devices',devices:devices});}
        else if(message.command==='devices'){deliver({command:'devices',devices:devices});}
        else if(message.command==='stored_files'){deliver({command:'stored_files',device_id:message.device_id,files:[{name:'pieza-simulada.gcode',thumbnail:'',seconds:3680,weight:24.5,can_repeat:true,filaments:[{toolId:0,material:'PLA simulado',color:'#eeeeee',weight:24.5}]}]});}
        else if(message.command==='select'){config.device_id=message.device_id;deliver({command:'status',status:DEMO_STATUS});}
        else if(message.command==='bind_profile'){deliver({command:'profile_bound',profile:profile.name,device_id:message.device_id});}
        else if(message.command==='enable_agent'){deliver({command:'agent_enabled',profile:profile.name,device_id:message.device_id});}
        else if(message.command==='disconnect'){deliver({command:'disconnected'});}
        else if(message.command==='prepare_stored'){deliver({command:'print_prepared',job:{id:'synthetic-stored',stored_file:true,preview:'',device_id:'synthetic-device',device:devices[0],name:message.name,profile:profile.name,plate:1,tools:[0],size:null,has_thumbnail:false,slots:DEMO_STATUS.slots,filaments:[{toolId:0,material:'PLA simulado',color:'#eeeeee',weight:24.5}]}});}
        else if(message.command==='prepare_print'){deliver({command:'print_prepared',job:{id:'synthetic-review',device_id:'synthetic-device',device:devices[0],name:'ejemplo-simulado.gcode',profile:profile.name,plate:message.plate,tools:[4],size:102400,has_thumbnail:false,slots:DEMO_STATUS.slots,filaments:[{toolId:4,material:'PLA simulado',color:'#eeeeee'}],suggested_mappings:[{toolId:4,slotId:1}]}});}
        else if(message.command==='commit_print'){deliver({command:'print_progress',phase:'uploading',now:100,total:100});deliver({command:'print_dispatched',result:{name:'ejemplo-SIMULADO.gcode',dispatched:true,printer_started_verified:false}});}
        else if(message.command==='save'){config=Object.assign(config,message.config);deliver({command:'saved'});}
        else if(message.command==='status'){deliver({command:'status',status:DEMO_STATUS});}
        else{deliver({command:'error',error:'Vista simulada. Instala el wheel en OrcaSlicer para conectar una impresora.'});}
        deliver({command:'done'});
      }};
    })();
    </script>""".replace("PREVIEW_STATUS_DELAY", str(max(0, args.status_delay))).replace("DEMO_STATUS", status).replace("PREVIEW_CONFIG", json.dumps({
        "mode": "cloud" if args.account else "demo", "cloud_backend": "experimental", "python": "/usr/bin/python3",
        "device_id": "synthetic-device" if args.account else "", "sdk_library": "SDK simulado",
        "sdk_settings": "DAT simulado", "profile_bindings": {}}))
    html = render_ui()
    html = html.replace("<main>", '<main><p class="warning">DEMOSTRACIÓN: cuenta, impresora y envío simulados. Ningún contacto con FlashForge.</p>')
    html = html.replace("<script>", bridge + "<script>", 1).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/favicon.ico"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Preview: http://127.0.0.1:{server.server_port}/ (datos simulados)", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
