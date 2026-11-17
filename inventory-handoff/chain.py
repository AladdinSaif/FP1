import json, re
from collections import defaultdict
from confirm_warn import call, bad, C
ctx={"allowed_company_ids":[124],"active_test":False}
LOC=735
C14="2026-11-14 20:59:59"; C15="2026-11-15 20:59:59"
def asof_by_lot(cut):
    res=defaultdict(float)
    for side,sign in (("location_dest_id",1),("location_id",-1)):
        g=call("stock.move.line/read_group",{"domain":[["state","=","done"],["date","<=",cut],[side,"=",LOC],["company_id","=",124]],"fields":["quantity_product_uom:sum"],"groupby":["product_id","lot_id"],"lazy":False,"context":ctx})
        if bad(g): raise SystemExit(json.dumps(g)[:300])
        for r in g: res[(r['product_id'][0],(r['lot_id'] or [0])[0])]+=sign*r['quantity_product_uom']
    return res
a14=asof_by_lot(C14); a15=asof_by_lot(C15)
q=call("stock.quant/search_read",{"domain":[["company_id","=",124],["location_id","=",LOC],["inventory_quantity_set","=",True]],"fields":["product_id","lot_id","quantity","inventory_quantity","product_categ_id"],"context":ctx})
prod={}
for r in q:
    pid=r['product_id'][0]; lot=(r['lot_id'] or [0])[0]
    p=prod.setdefault(pid,{'name':re.sub(r'^\[[^\]]*\]\s*','',r['product_id'][1]),'cat':r['product_categ_id'][1].replace('RH / ',''),'cnt':0.0,'asof15_counted':0.0,'now_counted':0.0,'lots':[]})
    p['cnt']+=r['inventory_quantity']; p['asof15_counted']+=a15.get((pid,lot),0.0); p['now_counted']+=r['quantity']; p['lots'].append(lot)
# post-cutoff movements per product (recorded after the count)
mv=call("stock.move.line/search_read",{"domain":[["state","=","done"],["date",">",C15],["company_id","=",124],"|",["location_id","=",LOC],["location_dest_id","=",LOC],["product_id","in",list(prod)]],"fields":["product_id","quantity_product_uom","location_id","location_dest_id","reference","date"],"context":ctx})
post=defaultdict(lambda:defaultdict(float))
for m in mv:
    src,dst=m['location_id'][1],m['location_dest_id'][1]; pid=m['product_id'][0]; qty=m['quantity_product_uom']
    if dst.startswith('BKY') and 'Production' in src: post[pid]['production_in']+=qty
    elif src.startswith('BKY') and 'Production' in dst: post[pid]['consumed']-=qty
    elif src.startswith('BKY') and 'Customers' in dst: post[pid]['delivered']-=qty
    elif 'Customers' in src and dst.startswith('BKY'): post[pid]['returned']+=qty
    elif 'Vendors' in src: post[pid]['received']+=qty
    elif 'Inventory' in src or 'Inventory' in dst: post[pid]['adjust']+=qty if dst.startswith('BKY') else -qty
    else: post[pid]['other']+= qty if dst.startswith('BKY') else -qty
# product-level opening / closing as-of
pids=list(prod)
def qty_at(t):
    ctx2={"allowed_company_ids":[124],"location":LOC}
    if t: ctx2["to_date"]=t
    return {x['id']:x['qty_available'] for x in call("product.product/read",{"ids":pids,"fields":["qty_available"],"context":ctx2})}
q14=qty_at(C14); q15=qty_at(C15); qnow=qty_at(None)
costs={x['id']:x['standard_price'] for x in call("product.product/read",{"ids":pids,"fields":["standard_price"],"context":ctx})}
rows=[]
for pid,p in prod.items():
    var=round(p['cnt']-p['asof15_counted'],4)
    rows.append({'pid':pid,'name':p['name'],'cat':p['cat'],'open14':q14[pid],'sys15':q15[pid],'counted_lots':p['cnt'],'sys15_counted_lots':p['asof15_counted'],'var':var,'val':var*costs[pid],'now':qnow[pid],'post':dict(post.get(pid,{}))})
json.dump(rows,open('chain_rows.json','w'),ensure_ascii=False)
print(len(rows),'products;',len([r for r in rows if abs(r['var'])>1e-6]),'with a variance at the count cutoff')
