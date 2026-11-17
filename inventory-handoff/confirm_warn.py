import json, subprocess
URL="https://bakery.brioche.sa/json/2/"
def call(mm, body):
    r=subprocess.run(["curl","-sS","-m","180","-X","POST",URL+mm,"-H","X-Odoo-Database: ebhashem-fp1-main-11606440","-H","Content-Type: application/json","-d",json.dumps(body)],capture_output=True,text=True)
    try: return json.loads(r.stdout)
    except Exception: return {"raw":r.stdout[:500]}
def bad(x): return isinstance(x,dict) and ('message' in x or 'raw' in x)
C={"allowed_company_ids":[124],"active_test":False}
def finish_with_warning(name, tol=0.001, force=False):
    mid=call("mrp.production/search",{"domain":[["name","=",name]],"context":C})[0]
    st=call("mrp.production/read",{"ids":[mid],"fields":["state"],"context":C})[0]['state']
    if st=='done': return f"{name}: already done"
    d=call("mrp.production/button_mark_done",{"ids":[mid],"context":C})
    if bad(d): return f"{name}: mark done failed: {(d.get('message') or d.get('raw'))[:250]}"
    if d is True or (isinstance(d,dict) and d.get('res_model')!='mrp.consumption.warning'):
        s=call("mrp.production/read",{"ids":[mid],"fields":["state"],"context":C})[0]['state']
        return f"{name}: {s.upper()} (res_model={d.get('res_model') if isinstance(d,dict) else d})"
    ctx=d['context']; lines=ctx['default_mrp_consumption_warning_line_ids']
    worst=0; desc=[]
    for _,_,l in lines:
        e=l['product_expected_qty_uom']; c=l['product_consumed_qty_uom']
        rel=abs(c-e)/e if e else (1 if c else 0); worst=max(worst,rel); desc.append((l['product_id'],round(e,5),round(c,5)))
    if worst>tol and not force: return f"{name}: warning too large ({worst:.2%}) {desc} -- NOT confirmed"
    w=call("mrp.consumption.warning/create",{"vals_list":[{"mrp_production_ids":[[6,0,ctx['default_mrp_production_ids']]],"mrp_consumption_warning_line_ids":lines}],"context":ctx})
    if bad(w): return f"{name}: wizard create failed: {(w.get('message') or w.get('raw'))[:250]}"
    r=call("mrp.consumption.warning/action_confirm",{"ids":[w[0]],"context":ctx})
    if bad(r): return f"{name}: confirm failed: {(r.get('message') or r.get('raw'))[:250]}"
    s=call("mrp.production/read",{"ids":[mid],"fields":["state"],"context":C})[0]['state']
    return f"{name}: {s.upper()} (warning worst deviation {worst:.4%})" if s=='done' else f"{name}: after confirm state={s}, result={json.dumps(r)[:300]}"
