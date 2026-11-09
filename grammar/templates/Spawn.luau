
do -- Spawn: neutral SpawnLocation on its platform's top centre, no force field
    local p = ws_part("SpawnLocation", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, Color3.fromRGB(13, 105, 172), 0, true)
    p.Neutral = true
    p.Duration = 0
    p.Enabled = true
    ws_tag(p, {{id:id}}, "Spawn")
end
