
do -- Wall: solid box
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(99, 95, 98), 0, true)
    ws_tag(p, {{id:id}}, "Wall")
end
