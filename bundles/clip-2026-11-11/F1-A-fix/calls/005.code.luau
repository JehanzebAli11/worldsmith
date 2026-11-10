-- Worldsmith P0 controller (fixed, reviewed template; WS-04, ADR 0004 and ADR 0005).
-- Server-scripted Humanoid:MoveTo waypoints; never jumps. A substrate probe, not an Autopilot
-- or agent result. Sent as one server script per attempt via run_script_in_play_mode.
-- Slots: the run nonce, the frozen route's waypoint numbers and the declared timing only.
-- It resets the character once at Worldsmith.spawn (LoadCharacter), then only calls MoveTo:
-- no teleport, no WalkSpeed/JumpHeight/gravity change, no Jump.
-- Reports by printing its result as JSON in chunks, WSP|<nonce>|RESULT|<i>|<n>|<chunk>, and
-- returns WSP|<nonce>|DONE|<n> (the tool's result carries the printed lines; ADR 0005).
local NONCE = "e7d965a21dc36b64"
local T_ATTEMPT = 60
local T_WAYPOINT = 10
local SETTLE = 2
local TICK = 0.05
local WAYPOINTS = {
    { "corridor", 0, 0, 20 },
    { "ledge_entry", -6, 0, 24 },
    { "lever_zone", -13, 0, 24 },
    { "corridor_mid", 0, 0, 24 },
    { "before_gate", 0, 0, 31 },
    { "past_gate", 0, 0, 35.5 },
    { "bridge", 0, 0, 40 },
    { "landing", 0, 0, 55 },
    { "goal", 0, 0, 60 },
}

local t_start = os.clock()
local deadline = t_start + T_ATTEMPT
local HttpService = game:GetService("HttpService")
local LogService = game:GetService("LogService")
local Players = game:GetService("Players")

local params = { t_attempt = T_ATTEMPT, t_waypoint = T_WAYPOINT, settle = SETTLE, tick = TICK, waypoints = {} }
for _, w in ipairs(WAYPOINTS) do
    table.insert(params.waypoints, { name = w[1], pos = { w[2], w[3], w[4] } })
end
local result = {
    nonce = NONCE, params = params, status = "running", detail = "", events = {}, waypoints = {},
    died = false, removed = false, jumps = 0, ticks = 0, scene_hash = "", t_died = -1, pos_died = "na",
    last_supported = "na", start_pos = "na", t_route_start = -1, events_dropped = 0, characters_added = 0,
}

-- Capture this run's event lines from the server log (prefix and nonce exact).
local prefix = "WS|" .. NONCE .. "|"
local goal_seen = false
local log_conn = LogService.MessageOut:Connect(function(message)
    if string.sub(message, 1, #prefix) == prefix then
        if #result.events < 64 then
            table.insert(result.events, message)
        else
            result.events_dropped += 1
        end
        if string.find(message, "|GOAL_TOUCHED|", 1, true) then
            goal_seen = true
        end
    end
end)

local CHUNK = 800
local function finish(status, detail)
    result.status = status
    result.detail = detail or ""
    result.t_end = os.clock() - t_start
    log_conn:Disconnect()
    local s = HttpService:JSONEncode(result)
    local n = math.max(1, math.ceil(#s / CHUNK))
    for i = 1, n do
        print(string.format("WSP|%s|RESULT|%d|%d|%s", NONCE, i, n, string.sub(s, (i - 1) * CHUNK + 1, i * CHUNK)))
    end
    return "WSP|" .. NONCE .. "|DONE|" .. n
end

local function tick()
    task.wait(TICK)
    result.ticks += 1
end

local root = workspace:FindFirstChild("Worldsmith")
if not root then
    return finish("harness_error", "no Workspace.Worldsmith")
end
local origin = root:GetAttribute("ws_origin")
local spawn = root:FindFirstChild("spawn")
if typeof(origin) ~= "Vector3" or not spawn or not spawn:IsA("SpawnLocation") then
    return finish("harness_error", "Worldsmith has no ws_origin or spawn")
end
result.scene_hash = tostring(root:GetAttribute("ws_scene_hash"))
root:SetAttribute("ws_nonce", NONCE)

local function rel(p)
    local q = p - origin
    return string.format("%.2f,%.2f,%.2f", q.X, q.Y, q.Z)
end

local player = Players:GetPlayers()[1]
while not player and os.clock() < deadline do
    tick()
    player = Players:GetPlayers()[1]
end
if not player then
    return finish("deadline", "no player")
end
-- Wait for the auto-loaded character first, so it cannot replace ours later; then reset once
-- at Worldsmith.spawn and wait for that new character (all bounded by the deadline).
local old = player.Character
while not (old and old.Parent) and os.clock() < deadline do
    tick()
    old = player.Character
end
player.CharacterAdded:Connect(function()
    result.characters_added += 1
end)
player.RespawnLocation = spawn
player:LoadCharacter()
local char, hum, hrp
while os.clock() < deadline do
    char = player.Character
    if char and char ~= old and char.Parent then
        hum = char:FindFirstChildOfClass("Humanoid")
        hrp = char:FindFirstChild("HumanoidRootPart")
        if hum and hrp then
            break
        end
    end
    tick()
end
if not (hum and hrp) then
    return finish("deadline", "no character")
end

local function dead()
    if result.died or hum.Health <= 0 then
        return true
    end
    if char.Parent == nil then
        result.removed = true
        return true
    end
    return false
end
local function where()
    local ok, s = pcall(function()
        return rel(hrp.Position)
    end)
    return ok and s or "na"
end
hum.Died:Connect(function()
    if not result.died then
        result.died = true
        result.t_died = os.clock() - t_start
        result.pos_died = where()
    end
end)
hum.StateChanged:Connect(function(_, new)
    if new == Enum.HumanoidStateType.Jumping then
        result.jumps += 1
    end
end)
local function watch()
    if not dead() and hum.FloorMaterial ~= Enum.Material.Air then
        result.last_supported = where()
    end
    tick()
end

-- Settle on the spawn (bounded by the deadline), then run the route.
local t_settle = os.clock()
while os.clock() - t_settle < 1 and os.clock() < deadline do
    watch()
end
result.start_pos = where()
result.t_route_start = os.clock() - t_start
for i, w in ipairs(WAYPOINTS) do
    if dead() or os.clock() >= deadline then
        break
    end
    local done, reached = false, false
    local conn = hum.MoveToFinished:Connect(function(r)
        done = true
        reached = r
    end)
    hum:MoveTo(origin + Vector3.new(w[2], w[3], w[4]))
    local t_wp = os.clock()
    while not done and not dead() and os.clock() - t_wp < T_WAYPOINT and os.clock() < deadline do
        watch()
    end
    conn:Disconnect()
    table.insert(result.waypoints, {
        i = i, name = w[1], reached = reached, died = dead(), t = os.clock() - t_start, pos = where(),
    })
end

-- Let the last events print (bounded by SETTLE and the deadline), then report.
local t_end = os.clock()
while os.clock() - t_end < SETTLE and os.clock() < deadline and not (goal_seen and not dead()) do
    watch()
end
if dead() then
    return finish("died", "")
end
if os.clock() >= deadline then
    return finish("deadline", "T_ATTEMPT reached")
end
return finish("route_done", goal_seen and "goal event seen" or "")
