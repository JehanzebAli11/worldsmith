-- Worldsmith read-back (fixed template, no substitution). Prints the Workspace.Worldsmith tree
-- as JSON in WS_READBACK|i|n|<chunk> lines. Positions are in the scene frame (minus ws_origin).
local HttpService = game:GetService("HttpService")
local roots = {}
for _, c in ipairs(workspace:GetChildren()) do
    if c.Name == "Worldsmith" then
        table.insert(roots, c)
    end
end
local function r3(v)
    return math.round(v * 1000) / 1000
end
local function vec(v)
    return { r3(v.X), r3(v.Y), r3(v.Z) }
end
local function attrs(inst)
    local a = {}
    for k, v in pairs(inst:GetAttributes()) do
        if typeof(v) == "Vector3" then
            a[k] = vec(v)
        else
            a[k] = v
        end
    end
    return a
end
local out = { roots = #roots, nodes = {} }
if #roots == 1 then
    local root = roots[1]
    local origin = root:GetAttribute("ws_origin") or Vector3.zero
    out.root = { class = root.ClassName, attrs = attrs(root) }
    for _, d in ipairs(root:GetDescendants()) do
        local path = d.Name
        local p = d.Parent
        while p and p ~= root do
            path = p.Name .. "/" .. path
            p = p.Parent
        end
        local node = { path = path, class = d.ClassName, attrs = attrs(d) }
        if d:IsA("BasePart") then
            node.size = vec(d.Size)
            node.pos = vec(d.Position - origin)
            node.rot = vec(d.Orientation)
            node.anchored = d.Anchored
            node.can_collide = d.CanCollide
        elseif d:IsA("LuaSourceContainer") then
            node.source_len = #d.Source
        end
        table.insert(out.nodes, node)
    end
end
local json = HttpService:JSONEncode(out)
local CHUNK = 900
local n = math.max(1, math.ceil(#json / CHUNK))
for i = 1, n do
    print(string.format("WS_READBACK|%d|%d|%s", i, n, string.sub(json, (i - 1) * CHUNK + 1, i * CHUNK)))
end
