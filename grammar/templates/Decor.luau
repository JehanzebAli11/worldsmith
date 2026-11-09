
do -- Decor: non-gameplay prop from a fixed palette; never collides
    local palette = {{enum:palette}}
    local colors = {
        crate = Color3.fromRGB(150, 110, 60),
        pillar = Color3.fromRGB(200, 200, 190),
        lamp = Color3.fromRGB(255, 220, 120),
        plant = Color3.fromRGB(60, 140, 60),
        banner = Color3.fromRGB(40, 80, 160),
    }
    local p = ws_part("Part", {{id:id}}, root, {{num:sx}}, {{num:sy}}, {{num:sz}}, {{num:px}}, {{num:py}}, {{num:pz}}, 0, colors[palette], 0, false)
    ws_tag(p, {{id:id}}, "Decor")
    p:SetAttribute("ws_palette", palette)
end
