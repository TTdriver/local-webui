"""Local LoRA controls; update only loader inputs in the saved workflow."""
import copy
import json
import subprocess
from pathlib import Path


def loaders(workflow):
    return {key: node for key, node in workflow.items()
            if node.get('class_type') in ('LoraLoaderModelOnly', 'LoraLoader')}


def changed_workflow(workflow, changes, filenames):
    updated = copy.deepcopy(workflow)
    nodes = loaders(updated)
    for key, values in changes.items():
        if key not in nodes or values['filename'] not in filenames:
            raise ValueError('The selected LoRA is no longer available. Reload settings.')
        strength = float(values['strength'])
        if not 0 <= strength <= 2:
            raise ValueError('Strength must be between 0 and 2.')
        nodes[key]['inputs']['lora_name'] = values['filename']
        nodes[key]['inputs']['strength_model'] = strength
    return updated


def available_files():
    result = subprocess.run(['docker', 'inspect', 'ollama-pocket-comfyui', '--format', '{{json .Mounts}}'],
                            capture_output=True, text=True, check=True, timeout=15)
    for mount in json.loads(result.stdout):
        if mount['Destination'] == '/opt/ComfyUI/models':
            folder = Path(mount['Source']) / 'loras'
            return sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*.safetensors'))
    raise RuntimeError('Could not find the installed ComfyUI LoRA folder.')


SCRIPT = r'''
import os,json,asyncio,sys
from pathlib import Path
from datetime import datetime,timezone
os.environ['WEBUI_SECRET_KEY']=Path('/app/backend/.webui_secret_key').read_text().strip()
from open_webui.models.config import Config
async def main():
    request=json.load(sys.stdin)
    values=await Config.get_many('image_generation.comfyui.workflow')
    current=json.loads(values['image_generation.comfyui.workflow'])
    if request['action']=='save':
        if current!=request['original']:
            raise ValueError('The workflow changed while settings were open. Close and reopen LoRA settings.')
        new=request['updated']
        # Independently reject any change beyond existing LoRA filename/model strength inputs.
        check=json.loads(json.dumps(new))
        if set(check)!=set(current): raise ValueError('Unexpected workflow change.')
        for key,node in current.items():
            if node.get('class_type') in ('LoraLoaderModelOnly','LoraLoader'):
                check[key]['inputs']['lora_name']=node['inputs']['lora_name']
                check[key]['inputs']['strength_model']=node['inputs']['strength_model']
        if check!=current: raise ValueError('Unexpected workflow change.')
        backup=Path('/app/backend/data/lora-settings-backups')
        backup.mkdir(mode=0o700,exist_ok=True)
        path=backup/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
        path.write_text(json.dumps(current));path.chmod(0o600)
        await Config.upsert({'image_generation.comfyui.workflow':json.dumps(new)})
        current=new
    print('LORA_RESULT:'+json.dumps(current))
asyncio.run(main())
'''


def request_config(action='load', **data):
    # The desktop owner already manages these local Docker services. No credentials
    # or other image-provider settings leave the Open WebUI container.
    result = subprocess.run(['docker', 'exec', '-i', '-e', 'PYTHONPATH=/app/backend',
                             'open-webui', 'python', '-c', SCRIPT],
                            input=json.dumps({'action': action, **data}), capture_output=True,
                            text=True, timeout=30)
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else 'Open WebUI is unavailable. Check that it is running.')
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('LORA_RESULT:'):
            return json.loads(line[len('LORA_RESULT:'):])
    raise RuntimeError('Open WebUI did not return its LoRA settings.')


def trigger_for(filename):
    for info in (Path.home() / '.local/share/local-lora/jobs').glob('*/job.json'):
        if filename.startswith('personal_' + info.parent.name + '_'):
            try:
                return json.loads(info.read_text()).get('trigger', '')
            except (ValueError, OSError):
                pass
    return ''
