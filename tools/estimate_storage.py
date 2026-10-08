"""Fail-closed storage boundary for estimate CLI inputs and managed outputs."""
import importlib.util
from pathlib import Path


def _service():
    spec=importlib.util.spec_from_file_location('estimate_ai_station_storage','/opt/ai-station/storage.py')
    service=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    return service,service.policy()


def _canonical(path,service):
    path=Path(path).absolute()
    if path.is_symlink() or path.resolve()!=path:
        raise service.StorageError('FAIL CLOSED: source/output symlink or traversal rejected')
    return path


def authorized_input(path):
    """Explicit caller-supplied file only; never traverse or ingest excluded disks."""
    service,cfg=_service()
    path=_canonical(path,service)
    service.ingestion_allowed(path,cfg)
    if not path.is_file():raise service.StorageError('Input must be an existing regular file')
    # Validate the physical managed mount for managed inputs. User attachments in
    # HOME/downloads stay ordinary explicitly provided inputs, not managed storage.
    for kind in ('research','derived','staging','source_document','corpus','dataset'):
        root=Path(cfg['mappings']['estimates'][kind]['path'])
        if path.is_relative_to(root):
            service.resolve_storage('estimates',kind,cfg=cfg)
            break
    return path


def _output_scope(path,service,cfg):
    path=_canonical(path,service)
    service.ingestion_allowed(path,cfg)
    for kind in ('research','derived','staging'):
        root=Path(cfg['mappings']['estimates'][kind]['path'])
        if path!=root and path.is_relative_to(root):
            resolved=service.resolve_storage('estimates',kind,write=True,cfg=cfg)
            if not path.is_relative_to(resolved):raise service.StorageError('Resolved output scope changed')
            return path,kind
    raise service.StorageError('FAIL CLOSED: estimate outputs require resolved research/derived/staging; no fallback')


def managed_output(path,data_id):
    """Validate write placement before caller mkdir/write; return canonical Path.

    Call register_output only after a successful write. This function creates nothing.
    """
    if not isinstance(data_id,str) or not data_id.strip():raise ValueError('Registry data_id required')
    service,cfg=_service()
    path,kind=_output_scope(path,service,cfg)
    if path.exists() and not path.is_file():raise service.StorageError('Output must be a regular file')
    return path


def register_output(path,data_id,*,derived_from=None):
    """Register actual bytes after write; validation repeats to avoid stale scope."""
    if not isinstance(data_id,str) or not data_id.strip():raise ValueError('Registry data_id required')
    service,cfg=_service()
    path,kind=_output_scope(path,service,cfg)
    if not path.is_file():raise service.StorageError('Cannot register missing/nonfile output')
    return service.register({'data_id':data_id,'name':path.name,'project':'estimates',
      'data_class':kind,'canonical_path':str(path),'source_type':'deterministic-estimate-cli',
      'owner_component':'smetchik','derived_from':derived_from,
      'notes':'Deterministic CLI output; source documents unchanged'},cfg)
