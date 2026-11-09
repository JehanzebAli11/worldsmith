
do -- Bridge: anchored at the gap's from edge, top flush with the from platform
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(124, 92, 70), 0, true)
    ws_tag(p, {{id:id}}, "Bridge")
end
