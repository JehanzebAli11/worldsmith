
do -- Gate: solid until its lever is active, then open (the gate event script does that)
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(245, 205, 48), 0, true)
    ws_tag(p, {{id:id}}, "Gate")
    p:SetAttribute("ws_lever", {{id:lever}})
    p:SetAttribute("ws_open", false)
    ws_events(p, "gate")
end
