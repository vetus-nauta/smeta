"""Process a specifically authorized WebUI upload; no arbitrary path reader."""
import base64
import json
import subprocess
import sys
from pathlib import Path
from .core import SUPPORTED, digest, json_write, make_chunks, now, Store
from .sources import source_meta
from .retrieval import answer

def analyse_upload(store,ollama,config,body):
    name=Path(str(body.get('filename',''))).name
    suffix=Path(name).suffix.lower()
    estimate_specific=(suffix in {'.7z','.zip','.gsfx','.gge','.json'} or
        suffix=='.xlsx' and any(word in body.get('query','').lower() for word in ('смет','вор','кац','норматив','объём','объем')))
    if estimate_specific:
        from .estimate_workflow import upload
        return upload(body,config)
    if Path(name).suffix.lower() not in SUPPORTED:raise ValueError('Unsupported uploaded document type')
    content=base64.b64decode(body['content_base64'],validate=True)
    if not 0<len(content)<=20*1024*1024:raise ValueError('Upload size limit exceeded')
    identifier=digest(content);root=store.root/'document-sessions'/identifier;root.mkdir(parents=True,exist_ok=True)
    service=None
    if config.get('storage_policy_required'):
        from .storage import station
        service=station()
        source_root=service.resolve_storage('estimates','source_document',write=True,required_bytes=len(content))/'user-uploads'/identifier
        source_root.mkdir(parents=True,exist_ok=True)
        path=source_root/('upload'+Path(name).suffix.lower())
    else:path=root/('upload'+Path(name).suffix.lower())
    if not path.exists():path.write_bytes(content)
    derived=service.resolve_storage('estimates','derived',write=True)/'user-uploads'/identifier if service else root/'extraction'
    if service:
        service.register({'data_id':'upload:'+identifier,'name':name,'project':'estimates','data_class':'source_document','canonical_path':str(path),'source_type':'webui_upload','source_uri':'webui-upload:'+body['file_id'],'read_only_source':True,'provenance_required':True})
    if not (derived/'segments.json').exists():
        import os
        with (root/'extraction.log').open('a') as log:
            subprocess.run([sys.executable,'-m','agent.extract',str(path),str(derived),config['docling_artifacts']],
                check=True,timeout=600,stdout=log,stderr=subprocess.STDOUT,
                env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1'))
    meta=source_meta('user_upload',body['file_id'],name,'webui-upload:'+body['file_id'],identifier,
                    source_class='unknown',project='user_document_session')
    if service:service.register({'data_id':'upload-derived:'+identifier,'name':name+' extraction','project':'estimates','data_class':'derived','canonical_path':str(derived),'derived_from':'upload:'+identifier,'source_type':'docling'})
    chunks=make_chunks(json.loads((derived/'segments.json').read_text()),meta,config,identifier)
    session=Store(root/'kb');doc,needs=session.discover(meta,'document-session-v2')
    if needs:
        for chunk in chunks:chunk['analysis']=ollama.analyse(chunk['text'])
        from .academic_worker import CARD_PROMPT,validate_cards
        cards=[]
        for chunk in chunks:
            accepted,_=validate_cards(ollama.generate(CARD_PROMPT,{'text':chunk['text'],'source_class':'unknown'}),chunk)
            cards.extend(accepted)
        json_write(root/'knowledge-cards.json',cards)
        from .lexicon import Lexicon
        lexical=Lexicon(session.root).process(chunks,ollama,body['file_id'],publish=True)
        json_write(root/'lexical-acceptance.json',lexical)
        vectors=ollama.embed([c['text'] for c in chunks])
        for c,v in zip(chunks,vectors):c['embedding']=v
        session.publish(doc,chunks)
    result=answer(session,ollama,body['query'])
    result['mode']='document_analysis';result['source_sha256']=identifier
    json_write(root/'report.json',{'time':now(),'filename':name,'chunks':len(chunks),'cloud_llm_calls':0,
                                  'kb_promotion':False,'query':body['query'],'sources':result['sources']})
    session.db.close();return result
