"""Shared image model selection with complete, compatible ComfyUI workflows."""
import json
import subprocess
from pathlib import Path

FIELDS = {
    'studio': ['image_generation.model', 'image_generation.comfyui.workflow', 'image_generation.comfyui.nodes', 'image_generation.steps', 'image_generation.engine'],
    'editor': ['images.edit.model', 'images.edit.comfyui.workflow', 'images.edit.comfyui.nodes', 'images.edit.engine'],
}


def model_files():
    result = subprocess.run(['docker', 'inspect', 'ollama-pocket-comfyui', '--format', '{{json .Mounts}}'], capture_output=True, text=True, check=True, timeout=15)
    root = next(Path(m['Source']) for m in json.loads(result.stdout) if m['Destination'] == '/opt/ComfyUI/models')
    return {folder: sorted(p.relative_to(root / folder).as_posix() for p in (root / folder).rglob('*.safetensors')) for folder in ['diffusion_models', 'text_encoders', 'vae', 'loras', 'checkpoints']}


def dependencies_present(workflow, files):
    types = {'UNETLoader': ('unet_name', 'diffusion_models'), 'CLIPLoader': ('clip_name', 'text_encoders'), 'VAELoader': ('vae_name', 'vae'), 'CheckpointLoaderSimple': ('ckpt_name', 'checkpoints')}
    for node in workflow.values():
        field = types.get(node.get('class_type'))
        if field and node['inputs'].get(field[0]) not in files.get(field[1], []):
            return False
    return True


SCRIPT = r'''
import asyncio,json,sys,os
from pathlib import Path
from datetime import datetime,timezone
os.environ['WEBUI_SECRET_KEY']=Path('/app/backend/.webui_secret_key').read_text().strip()
from open_webui.models.config import Config
async def main():
    request=json.load(sys.stdin)
    fields=request['fields']; keys=[key for group in fields.values() for key in group]
    current=await Config.get_many(*keys)
    state_path=Path('/app/backend/data/image-settings-workflows.json')
    registry=json.loads(state_path.read_text()) if state_path.exists() else {'studio':{},'editor':{}}
    catalog=json.loads(json.dumps(request['templates']))
    for slot,group in fields.items():
        catalog[slot].update(registry.get(slot,{}))
        name=current.get(group[0])
        if name and current.get(group[1]):
            config={key:current[key] for key in group if key in current}
            catalog[slot][name]={'label':catalog[slot].get(name,{}).get('label',name),'config':config}
    if request['action']=='save':
        if current!=request['original']:raise ValueError('Image settings changed while this menu was open. Close and reopen Image Settings.')
        updates={}
        for slot,selected in request['selected'].items():
            if selected not in catalog[slot]:raise ValueError('Unknown image model.')
            group=fields[slot]; entry=catalog[slot][selected]
            # A saved selection must supply a full workflow for this engine only.
            config=entry['config']
            if set(config)!=set(group) or config[group[0]]!=selected:raise ValueError('Incomplete model configuration.')
            workflow=json.loads(config[group[1]])
            types={'UNETLoader':('unet_name','diffusion_models'),'CLIPLoader':('clip_name','text_encoders'),'VAELoader':('vae_name','vae'),'CheckpointLoaderSimple':('ckpt_name','checkpoints')}
            for node in workflow.values():
                pair=types.get(node.get('class_type'))
                if pair and node['inputs'][pair[0]] not in request['files'][pair[1]]:raise ValueError('A required model file is missing. Reload settings.')
            config=json.loads(json.dumps(config))
            changes=request.get('loras',{}).get(slot,{})
            for node_id,values in changes.items():
                node=workflow.get(node_id,{})
                if node.get('class_type') not in ('LoraLoader','LoraLoaderModelOnly'):raise ValueError('Unknown LoRA slot.')
                if values['filename'] not in request['files']['loras']:raise ValueError('LoRA file is missing.')
                strength=float(values['strength'])
                if not 0<=strength<=2:raise ValueError('Strength must be between 0 and 2.')
                node['inputs']['lora_name']=values['filename']
                node['inputs']['strength_model']=strength
            config[group[1]]=json.dumps(workflow)
            updates.update(config)
        backup=Path('/app/backend/data/image-settings-backups');backup.mkdir(mode=0o700,exist_ok=True)
        p=backup/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json');p.write_text(json.dumps(current));p.chmod(0o600)
        # Store the active workflows, including current LoRA choices, before switching.
        for slot,group in fields.items():
            if current.get(group[0]):registry[slot][current[group[0]]]=catalog[slot][current[group[0]]]
        state_path.write_text(json.dumps(registry));state_path.chmod(0o600)
        await Config.upsert(updates)
        current.update(updates)
    print('IMAGE_RESULT:'+json.dumps({'current':current,'catalog':catalog}))
asyncio.run(main())
'''


def request_settings(action='load', **data):
    files = model_files()
    templates = json.loads(Path(__file__).with_name('image_workflows.json').read_text())
    result = subprocess.run(['docker', 'exec', '-i', '-e', 'PYTHONPATH=/app/backend', 'open-webui', 'python', '-c', SCRIPT], input=json.dumps({'action': action, 'fields': FIELDS, 'files': files, 'templates': templates, **data}), capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'Open WebUI is unavailable.')
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('IMAGE_RESULT:'):
            state = json.loads(line[len('IMAGE_RESULT:'):])
            for slot, choices in state['catalog'].items():
                state['catalog'][slot] = {name: entry for name, entry in choices.items() if dependencies_present(json.loads(entry['config'][FIELDS[slot][1]]), files)}
            return state
    raise RuntimeError('Open WebUI did not return image settings.')
