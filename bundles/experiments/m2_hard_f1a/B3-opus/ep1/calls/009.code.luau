-- Worldsmith P0 controller, player version p0.v3 (fixed, reviewed template; ADR 0012).
-- p0.v3 = p0.v2 (runner/templates/p0_controller_v2.luau, unchanged) with only the spawn check
-- changed: it counts physics steps (RunService.Heartbeat), not wall-clock ticks. A warm-up of
-- WARM_FRAMES steady Heartbeat steps, then grounded (FloorMaterial or a downward ray) within
-- GROUND_STEPS steps, then a SETTLE_STEPS-step settle within MAX_OFFSET stud (XZ) of the spawn
-- centre. Failures: "not_warm", "not_grounded", "moved_before_route" (a real offset failure).
-- Server-scripted Humanoid:MoveTo waypoints; never jumps. A substrate probe, not an Autopilot
-- or agent result. Sent as one server script per attempt via run_script_in_play_mode.
-- Slots: the run nonce, the frozen route's waypoint numbers and the declared timing only.
-- It resets the character once at Worldsmith.spawn (LoadCharacter), then only calls MoveTo:
-- no teleport, no WalkSpeed/JumpHeight/gravity change, no Jump.
-- Reports by printing its result as JSON in chunks, WSP|<nonce>|RESULT|<i>|<n>|<chunk>, and
-- returns WSP|<nonce>|DONE|<n> (the tool's result carries the printed lines; ADR 0005).
local NONCE = "79c97a333f1e7a8d"
local T_ATTEMPT = 60
local T_WAYPOINT = 10
local SETTLE = 2
local TICK = 0.05
local SAMPLE_EVERY = 1
local MAX_SAMPLES = 128
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

local params = { t_attempt = T_ATTEMPT, t_waypoint = T_WAYPOINT, settle = SETTLE, tick = TICK,
    sample_every = SAMPLE_EVERY, waypoints = {} }
for _, w in ipairs(WAYPOINTS) do
    table.insert(params.waypoints, { name = w[1], pos = { w[2], w[3], w[4] } })
end
local result = {
    nonce = NONCE, params = params, status = "running", detail = "", events = {}, waypoints = {},
    died = false, removed = false, jumps = 0, ticks = 0, scene_hash = "", t_died = -1, pos_died = "na",
    last_supported = "na", start_pos = "na", t_route_start = -1, events_dropped = 0, characters_added = 0,
    samples = {}, samples_dropped = 0,
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
-- Position samples (p0.v1): from the route start, while alive, one at the first watch tick at
-- least SAMPLE_EVERY - 2 * TICK after the previous one (so the tick's overshoot keeps
-- consecutive samples within SAMPLE_EVERY under normal frame pacing). Controller clock.
local route_started = false
local t_last_sample = 0
local function sample(now)
    if #result.samples < MAX_SAMPLES then
        table.insert(result.samples, { t = now - t_start, pos = where() })
    else
        result.samples_dropped += 1
    end
    t_last_sample = now
end
local function watch()
    if not dead() then
        if hum.FloorMaterial ~= Enum.Material.Air then
            result.last_supported = where()
        end
        local now = os.clock()
        if route_started and now - t_last_sample >= SAMPLE_EVERY - 2 * TICK then
            sample(now)
        end
    end
    tick()
end

-- Spawn check (p0.v3, ADR 0012): counts physics steps (RunService.Heartbeat), not wall-clock
-- ticks. p0.v2 waited GROUND_WAIT s of wall clock plus a 1 s settle; when a play session starts
-- slowly (0.22 to 0.4 s per task.wait(0.05)) few physics steps run in that time, FloorMaterial
-- can still read Air on a character standing on the spawn, and the check failed. Here:
-- 1. warm-up: WARM_FRAMES consecutive Heartbeat steps each with dt <= WARM_MAX_DT, bounded by
--    WARM_WAIT s of wall clock and the deadline; else harness_error "not_warm" (dts reported);
-- 2. grounded: within GROUND_STEPS Heartbeat steps (and the deadline), FloorMaterial ~= Air or a
--    downward ray from the HumanoidRootPart (excluding the character) hits a part within
--    HipHeight + RAY_EXTRA studs; else harness_error "not_grounded"; ground_test says which test;
-- 3. settle: SETTLE_STEPS Heartbeat steps, each within MAX_OFFSET stud (XZ) of the spawn centre
--    (spawn_offset, settle_drift, settle_max_offset as in p0.v2), else "moved_before_route";
--    grounded again at its end by the same test, else "not_grounded".
local RunService = game:GetService("RunService")
local MAX_OFFSET = 1
local WARM_FRAMES = 10
local WARM_MAX_DT = 0.1
local WARM_WAIT = 10
local GROUND_STEPS = 120
local SETTLE_STEPS = 30
local RAY_EXTRA = 1.5
local MAX_WARM_DTS = 64
local spawn_c = spawn.Position
result.spawn_pos = rel(spawn_c)
local function xz_from(c)
    local ok, d = pcall(function()
        local p = hrp.Position
        return math.sqrt((p.X - c.X) ^ 2 + (p.Z - c.Z) ^ 2)
    end)
    return ok and d or -1
end
local function r2(x)
    return math.floor(x * 100 + 0.5) / 100
end
local function r3(x)
    return math.floor(x * 1000 + 0.5) / 1000
end
-- One physics step: wait for the next Heartbeat; keep last_supported as watch() does.
local function step()
    local dt = RunService.Heartbeat:Wait()
    if not dead() and hum.FloorMaterial ~= Enum.Material.Air then
        result.last_supported = where()
    end
    return dt
end
local ray_params = RaycastParams.new()
ray_params.FilterType = Enum.RaycastFilterType.Exclude
ray_params.FilterDescendantsInstances = { char }
-- "floor" (FloorMaterial ~= Air), "ray" (a part within HipHeight + RAY_EXTRA below the root) or
-- "none"; the ray's hit distance (-1 = no ray hit used).
local function ground_test()
    local ok, test, dist = pcall(function()
        if hum.FloorMaterial ~= Enum.Material.Air then
            return "floor", -1
        end
        local hit = workspace:Raycast(hrp.Position, Vector3.new(0, -(hum.HipHeight + RAY_EXTRA), 0), ray_params)
        if hit and hit.Instance and hit.Instance:IsA("BasePart") then
            return "ray", hit.Distance
        end
        return "none", -1
    end)
    if not ok then
        return "none", -1
    end
    return test, dist
end

-- 1. Warm-up.
local warm_dts = {}
local warm_run, warm_frames, max_dt_warm = 0, 0, 0
local t_warm = os.clock()
while warm_run < WARM_FRAMES and not dead() and os.clock() - t_warm < WARM_WAIT and os.clock() < deadline do
    local dt = step()
    warm_frames += 1
    max_dt_warm = math.max(max_dt_warm, dt)
    if #warm_dts < MAX_WARM_DTS then
        table.insert(warm_dts, r3(dt))
    end
    if dt <= WARM_MAX_DT then
        warm_run += 1
    else
        warm_run = 0
    end
end
result.warm_frames = warm_frames
result.warm_wall_s = r2(os.clock() - t_warm)
result.max_dt_warm = r3(max_dt_warm)
result.warm_dts = warm_dts
if dead() then
    result.start_pos = where()
    return finish("harness_error", "died_before_route")
end
if warm_run < WARM_FRAMES then
    result.start_pos = where()
    local dts = {}
    for _, dt in ipairs(warm_dts) do
        table.insert(dts, string.format("%.3f", dt))
    end
    return finish("harness_error", string.format(
        "not_warm: warm_frames=%d warm_wall_s=%.2f max_dt_warm=%.3f warm_run=%d dts=%s",
        warm_frames, result.warm_wall_s, result.max_dt_warm, warm_run, table.concat(dts, ",")))
end

-- 2. Grounded.
local ground_steps = 0
local gtest, gdist = ground_test()
while gtest == "none" and not dead() and ground_steps < GROUND_STEPS and os.clock() < deadline do
    step()
    ground_steps += 1
    gtest, gdist = ground_test()
end
result.ground_steps = ground_steps
result.ground_test = gtest
result.ray_dist = gdist < 0 and -1 or r2(gdist)
if dead() then
    result.start_pos = where()
    return finish("harness_error", "died_before_route")
end
if gtest == "none" then
    result.start_pos = where()
    return finish("harness_error", string.format("not_grounded: before the settle, ground_steps=%d", ground_steps))
end

-- 3. Settle on the spawn (bounded by SETTLE_STEPS and the deadline), then run the route.
local p_settle = hrp.Position
local off0 = xz_from(spawn_c)
local off_max, drift = off0, 0
local settle_steps = 0
while settle_steps < SETTLE_STEPS and os.clock() < deadline and not dead() do
    step()
    settle_steps += 1
    local o, d = xz_from(spawn_c), xz_from(p_settle)
    if o < 0 or d < 0 then
        off_max, drift = -1, -1
        break
    end
    off_max, drift = math.max(off_max, o), math.max(drift, d)
end
result.settle_steps = settle_steps
result.start_pos = where()
result.spawn_offset = off0 < 0 and -1 or r2(off0)
result.settle_drift = drift < 0 and -1 or r2(drift)
result.settle_max_offset = off_max < 0 and -1 or r2(off_max)
if dead() then
    return finish("harness_error", "died_before_route")
end
local etest = ground_test()
result.ground_test_end = etest
if off0 < 0 or off_max < 0 or off_max > MAX_OFFSET then
    return finish("harness_error", string.format(
        "moved_before_route: spawn_offset=%.2f settle_drift=%.2f settle_max_offset=%.2f grounded=%s",
        result.spawn_offset, result.settle_drift, result.settle_max_offset, tostring(etest ~= "none")))
end
if etest == "none" then
    return finish("harness_error", string.format(
        "not_grounded: at the settle end, settle_steps=%d spawn_offset=%.2f settle_max_offset=%.2f",
        settle_steps, result.spawn_offset, result.settle_max_offset))
end
result.t_route_start = os.clock() - t_start
route_started = true
t_last_sample = t_start + result.t_route_start
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
