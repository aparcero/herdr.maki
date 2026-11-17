-- Test-only adapter: capture the real registrations, then exercise handlers
-- and UI hooks in Maki's task scope. Fault cases replace only the process API.
local specs = {}
local register = maki.api.register_tool
maki.api.register_tool = function(spec)
  specs[spec.name] = spec
  register(spec)
end
require("ast_grep.init")
maki.api.register_tool = register

register({
  name = "ast_grep_test",
  description = "Run an isolated ast-grep regression fixture.",
  permission = "run",
  schema = { type = "object", properties = { request = { type = "string", required = true } } },
  handler = function(input, ctx)
    local request = assert(maki.json.decode(input.request))
    if request.setup then
      local ok, err = pcall(require("ast_grep.init").setup, request.setup)
      return assert(maki.json.encode({ ok = ok, error = not ok and tostring(err) or nil }))
    end
    local spec = specs[request.tool or "ast_grep_rewrite"]
    local saved_start, saved_wait, saved_stop = maki.fn.jobstart, maki.fn.jobwait, maki.fn.jobstop
    local saved_session = maki.session.read
    local calls, stops, count_output = {}, 0, nil
    if request.results then
      maki.fn.jobstart = function(args, opts)
        calls[#calls + 1] = args
        count_output = opts.on_stdout
        return #calls
      end
      maki.fn.jobwait = function(id, timeout)
        assert(timeout == (request.timeout or 30000), "unexpected jobwait timeout " .. tostring(timeout))
        if request.overflow then count_output(id, string.rep("x", 16 * 1024 * 1024 + 1)) end
        local result = request.results[id]
        if result.timeout then return nil, "job wait timed out" end
        return result
      end
      maki.fn.jobstop = function() stops = stops + 1 end
    end
    if request.plan then maki.session.read = function() return { mode = "plan" } end end
    if request.deadline then assert(ctx:set_deadline(request.deadline)) end
    local ok, result = pcall(spec.handler, request.input, ctx)
    maki.fn.jobstart, maki.fn.jobwait, maki.fn.jobstop = saved_start, saved_wait, saved_stop
    maki.session.read = saved_session
    assert(ok, result)
    if request.render then
      local before = result.body:get_lines()
      result.body:click({ row = 1 })
      local after = result.body:get_lines()
      local restored = spec.restore(request.input, result.llm_output, result.is_error, {
        state = function() return result.state end,
        tool_output_lines = function() return ctx:tool_output_lines() end,
      })
      local header = spec.header(request.input)
      return assert(maki.json.encode({
        output = result.llm_output, before = before, after = after,
        restored = restored:get_lines(), header = header:get_lines(), state = result.state,
      }))
    end
    return assert(maki.json.encode({ output = result.llm_output, is_error = result.is_error, calls = #calls, stops = stops }))
  end,
})
