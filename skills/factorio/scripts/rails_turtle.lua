-- CFGJ = {start={x,y,dir}, cmds={...}, clear=true}
local cfg=helpers.json_to_table(CFGJ)
local res={rails={},marks={},signals={},blocked={},trees=0}
local function clear(p,r)
  for _,e in pairs(s.find_entities_filtered{area={{p.x-r,p.y-r},{p.x+r,p.y+r}},type={"tree","simple-entity"}}) do e.destroy() res.trees=res.trees+1 end
end
local function force_clear(p,r)
  local why={}
  for _,e in pairs(s.find_entities_filtered{area={{p.x-r,p.y-r},{p.x+r,p.y+r}}}) do
    if e.type=="cliff" then why.cliff=(why.cliff or 0)+1 e.destroy{do_cliff_correction=false}
    elseif e.type~="character" and e.type~="resource" and e.type~="straight-rail" and e.type~="curved-rail-a" and e.type~="curved-rail-b" and e.type~="half-diagonal-rail" and e.type~="rail-signal" then why[e.name]=(why[e.name] or 0)+1 e.destroy() end
  end
  local t={}
  for _,tl in pairs(s.find_tiles_filtered{area={{p.x-r,p.y-r},{p.x+r,p.y+r}},collision_mask="water_tile"}) do t[#t+1]={name="landfill",position=tl.position} end
  if #t>0 then why.water=#t s.set_tiles(t) end
  return why
end
local function place(name,position,direction)
  local e=s.create_entity{name=name,position=position,direction=direction,force="player"}
  if not e then clear(position,5) e=s.create_entity{name=name,position=position,direction=direction,force="player"} end
  if not e then
    local why=force_clear(position,5)
    res.blocked[#res.blocked+1]={x=position.x,y=position.y,why=why}
    e=s.create_entity{name=name,position=position,direction=direction,force="player"}
  end
  if not e then error("cannot place "..name.." at "..position.x..","..position.y) end
  -- tiles the rail really covers: its boxes are ORIENTED (bounding_box.orientation), so left_top/right_bottom
  -- alone miss most of a curve (a pole once landed on one)
  local tiles, seen = {}, {}
  local x1, y1, x2, y2 = 1e9, 1e9, -1e9, -1e9
  for _, b in pairs({e.bounding_box, e.secondary_bounding_box}) do
    if b then
      local cx, cy = (b.left_top.x + b.right_bottom.x) / 2, (b.left_top.y + b.right_bottom.y) / 2
      local hw, hh = (b.right_bottom.x - b.left_top.x) / 2, (b.right_bottom.y - b.left_top.y) / 2
      local a = (b.orientation or 0) * 2 * math.pi
      local ca, sa = math.cos(a), math.sin(a)
      local R = math.sqrt(hw * hw + hh * hh)
      for tx = math.floor(cx - R), math.ceil(cx + R) do
        for ty = math.floor(cy - R), math.ceil(cy + R) do
          local hit = false
          for _, o in pairs({{0.5, 0.5}, {0.1, 0.1}, {0.9, 0.1}, {0.1, 0.9}, {0.9, 0.9}}) do
            local dx, dy = tx + o[1] - cx, ty + o[2] - cy
            local lx, ly = dx * ca + dy * sa, -dx * sa + dy * ca      -- into the box's own frame
            if math.abs(lx) <= hw and math.abs(ly) <= hh then hit = true break end
          end
          if hit and not seen[tx .. "," .. ty] then
            seen[tx .. "," .. ty] = true
            tiles[#tiles + 1] = {tx, ty}
            x1 = math.min(x1, tx) y1 = math.min(y1, ty) x2 = math.max(x2, tx + 1) y2 = math.max(y2, ty + 1)
          end
        end
      end
    end
  end
  res.rails[#res.rails+1]={name=name,x=e.position.x,y=e.position.y,dir=e.direction,bb={x1,y1,x2,y2},tiles=tiles}
  return e
end
local r0=place("straight-rail",{x=cfg.start[1],y=cfg.start[2]},cfg.start[3])
local rd=cfg.start[4]==1 and defines.rail_direction.back or defines.rail_direction.front
local e=r0.get_rail_end(rd)
local back=r0.get_rail_end(rd==defines.rail_direction.front and defines.rail_direction.back or defines.rail_direction.front)
res.start_back=back.location
local RC=defines.rail_connection_direction
local function step(kind)
  local cur=e.location.direction
  local want=(kind=="S") and cur or ((kind=="L") and (cur+15)%16 or (cur+1)%16)
  local pick
  for _,x in pairs(e.get_rail_extensions("rail")) do if x.goal.direction==want and x.goal.rail_layer==0 then pick=x end end
  if not pick then error("no extension "..kind.." from dir "..cur) end
  -- reuse an existing rail (closing the loop)
  local ok=e.make_copy()
  local moved=ok.move_forward(kind=="S" and RC.straight or (kind=="L" and RC.left or RC.right))
  if moved then e=ok res.reused=(res.reused or 0)+1 return end
  place(pick.name,pick.position,pick.direction)
  if not e.move_forward(kind=="S" and RC.straight or (kind=="L" and RC.left or RC.right)) then error("move failed") end
end
for _,c in ipairs(cfg.cmds) do
  local k=c:sub(1,1)
  if k=="@" then
    local l=e.location
    res.marks[c:sub(2)]={x=l.position.x,y=l.position.y,dir=l.direction,rail={e.rail.position.x,e.rail.position.y}}
  elseif k=="!" then
    local l=e.out_signal_location
    local nm=c=="!c" and "rail-chain-signal" or "rail-signal"
    local sg=s.create_entity{name=nm,position=l.position,direction=l.direction,force="player"}
    if not sg then clear(l.position,2) sg=s.create_entity{name=nm,position=l.position,direction=l.direction,force="player"} end
    if sg then res.signals[#res.signals+1]={name=nm,x=sg.position.x,y=sg.position.y,dir=sg.direction} else res.blocked[#res.blocked+1]={x=l.position.x,y=l.position.y,why={signal=1}} end
  else
    for i=1,tonumber(c:sub(2)) do step(k) end
  end
end
res["end"]=e.location
res.closed=(e.location.position.x==back.location.position.x and e.location.position.y==back.location.position.y)
helpers.write_file("turtle.json",helpers.table_to_json(res))
out("end",serpent.line(e.location),"start_back",serpent.line(back.location),"rails",#res.rails,"blocked",#res.blocked,"trees",res.trees)
if cfg.post then local ch,err=load(cfg.post,"post","t",setmetatable({s=s,f=f,out=out,res=res,json=json,pos=pos},{__index=_ENV})) if not ch then out("POST COMPILE",err) else local ok,e2=pcall(ch) if not ok then out("POST ERROR",e2) end end end
