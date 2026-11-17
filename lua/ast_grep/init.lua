-- Native port of the former ast-grep MCP wrapper. No shell or server required.
local ToolView = require("maki.tool_view")
local truncate = require("maki.truncate")
local output_limits = require("maki.output_limits")
local serial = maki.async.semaphore(1)
local ARRAY_MT = getmetatable(assert(maki.json.decode("[]")))
local MAX_BYTES = 16 * 1024 * 1024
local PARTIAL = "\nThe apply command may have changed some files before failing; inspect the diff before retrying."
local FORMATS = { pattern = true, ast = true, cst = true, sexp = true }
local options = { timeout_ms = 30000 }

local function trim(text)
  return text:match("^%s*(.-)%s*$")
end

local properties = {
  pattern = { type = "string", description = "AST pattern: $NAME captures one node; $$$NAME captures multiple nodes.", required = true },
  language = { type = "string", description = "ast-grep language, e.g. cpp, rust, javascript, python.", required = true },
  paths = { type = "array", items = { type = "string" }, description = "Files/directories relative to Maki's working directory. Defaults to [\".\"]." },
  globs = { type = "array", items = { type = "string" }, description = "Include/exclude globs, e.g. [\"*.cpp\", \"!vendor/**\"]." },
}
local rewrite_properties = table.clone(properties)
rewrite_properties.rewrite = { type = "string", description = "Replacement with captured metavariables. Empty string removes the matched node.", required = true }
rewrite_properties.dry_run = { type = "boolean", description = "Defaults to true (preview). Set false only after reviewing the preview." }
local tree_properties = {
  code = { type = "string", description = "A pattern, or a snippet of the code it should match.", required = true },
  language = properties.language,
  format = { type = "string", enum = { "pattern", "ast", "cst", "sexp" }, description = "pattern (default): the node a search matches, with metavariables. ast: named nodes with field names and positions. cst: ast plus punctuation. sexp: S-expression." },
}

local function check_fields(input, allowed, required)
  if type(input) ~= "table" then error("arguments must be an object", 0) end
  for key in pairs(input) do
    if not allowed[key] then error("Unknown argument: " .. tostring(key), 0) end
  end
  for _, key in ipairs(required) do
    if type(input[key]) ~= "string" or trim(input[key]) == "" then
      error(key .. " must be a non-empty string", 0)
    end
  end
end

local function validate(input, rewrite)
  check_fields(input, rewrite and rewrite_properties or properties, { "pattern", "language" })
  for _, key in ipairs({ "paths", "globs" }) do
    local list = input[key]
    if list ~= nil then
      if type(list) ~= "table" or (next(list) == nil and getmetatable(list) ~= ARRAY_MT) then
        error(key .. " must be an array of non-empty strings", 0)
      end
      for i, value in pairs(list) do
        if type(i) ~= "number" or i % 1 ~= 0 or i < 1 or i > #list
          or type(value) ~= "string" or trim(value) == "" then
          error(key .. " must be an array of non-empty strings", 0)
        end
      end
      for i = 1, #list do
        if list[i] == nil then error(key .. " must not contain holes", 0) end
      end
    end
  end
  if input.paths and #input.paths == 0 then error("paths must not be empty; omit it to use the working directory", 0) end
  if rewrite then
    if type(input.rewrite) ~= "string" then error("rewrite must be a string (empty is allowed)", 0) end
    if input.dry_run ~= nil and type(input.dry_run) ~= "boolean" then error("dry_run must be a boolean", 0) end
  end
end

local function validate_tree(input)
  check_fields(input, tree_properties, { "code", "language" })
  if input.format ~= nil and not FORMATS[input.format] then
    error("format must be one of pattern, ast, cst, sexp", 0)
  end
end

local function binary()
  local value = maki.uv.os_getenv("ASTGREP_BIN")
  return (value and value ~= "") and value or "ast-grep"
end

-- Attached values (--pattern=-$A) keep a leading "-" from parsing as a flag.
local function argv(input, rewrite, apply)
  local args = { binary(), "run", "--pattern=" .. input.pattern, "--lang=" .. input.language, "--color", "never" }
  if rewrite then table.insert(args, "--rewrite=" .. input.rewrite) end
  -- ast-grep JSON describes proposed rewrites; applying must omit --json.
  table.insert(args, apply and "--update-all" or "--json=compact")
  for _, glob in ipairs(input.globs or {}) do table.insert(args, "--globs=" .. glob) end
  table.insert(args, "--")
  for _, path in ipairs(input.paths or { "." }) do table.insert(args, path) end
  return args
end

-- --stdin searches only the job's stdin, which Maki connects to the null
-- device, so printing the tree never walks the working directory.
local function tree_argv(input)
  return { binary(), "run", "--pattern=" .. input.code, "--lang=" .. input.language, "--color", "never",
    "--debug-query=" .. (input.format or "pattern"), "--stdin" }
end

local function run(args, operation)
  local bytes, overflow = 0, false
  local function count(id, line)
    bytes = bytes + #line + 1
    if bytes > MAX_BYTES then
      overflow = true
      maki.fn.jobstop(id)
    end
  end
  local id, err = maki.fn.jobstart(args, { scope = "task", on_stdout = count, on_stderr = count })
  if not id then error("Could not run ast-grep: " .. tostring(err), 0) end
  operation.job = id
  local result
  result, err = maki.fn.jobwait(id, options.timeout_ms)
  if not result then maki.fn.jobstop(id) end
  operation.job = nil
  if overflow then error("ast-grep output exceeded 16 MiB; narrow paths/globs", 0) end
  if not result then error("Could not run ast-grep: " .. tostring(err), 0) end
  if result.truncated then error("ast-grep output was truncated; cannot confirm results", 0) end
  return result
end

local function diagnostics(result)
  return truncate(trim(result.stderr .. "\n" .. result.stdout), nil, 8000)
end

local function matches_from(result, rewrite)
  if (result.exit_code ~= 0 and result.exit_code ~= 1) or trim(result.stderr) ~= "" then
    error("ast-grep search/preview failed (exit " .. tostring(result.exit_code) .. "):\n" .. diagnostics(result), 0)
  end
  local matches = maki.json.decode(result.stdout)
  if not matches then error("ast-grep returned invalid JSON:\n" .. diagnostics(result), 0) end
  if type(matches) ~= "table" or not result.stdout:match("^%s*%[")
    or (result.exit_code == 1 and #matches ~= 0) then
    error("ast-grep returned an unexpected search result", 0)
  end
  for _, match in ipairs(matches) do
    local start = type(match) == "table" and type(match.range) == "table" and match.range.start
    if type(match) ~= "table" or type(match.file) ~= "string" or type(match.text) ~= "string"
      or type(start) ~= "table" or type(start.line) ~= "number" or type(start.column) ~= "number" then
      error("ast-grep returned an invalid match", 0)
    end
    if rewrite and type(match.replacement) ~= "string" then error("ast-grep did not return replacement text", 0) end
  end
  return matches
end

local function format_matches(matches, rewrite, limit)
  local lines = {}
  for i = 1, math.min(#matches, limit) do
    local match = matches[i]
    local start = match.range.start
    local location = match.file .. ":" .. (start.line + 1) .. ":" .. (start.column + 1)
    lines[#lines + 1] = location .. "\n  " .. (rewrite and "matched: " or "") .. match.text
      .. (rewrite and "\n  replacement: " .. match.replacement or "")
  end
  if #matches > limit then
    lines[#lines + 1] = "Showing " .. limit .. " of " .. #matches .. " matches. Narrow paths/globs to inspect the rest."
  end
  return table.concat(lines, "\n\n")
end

local function view(text, is_error, ctx)
  local lines = {}
  for _, line in ipairs(maki.split(text, "\n")) do
    local style = is_error and "tool_error" or line:match("^  replacement:") and "diff_new"
      or line:match("^  matched:") and "diff_old" or line:match(":%d+:%d+$") and "path" or nil
    lines[#lines + 1] = style and { { line, style } } or line
  end
  local limits = ctx:tool_output_lines()
  return ToolView.restore_lines(lines, { max_lines = (limits and limits.grep) or 5, keep = "head", max_expand_lines = 5000, max_line_bytes = 2000 })
end

local function response(text, ui_text, is_error, ctx)
  local max_lines, max_bytes = output_limits.resolve({}, ctx)
  ui_text = truncate(ui_text or text, 5000, 512 * 1024)
  return {
    llm_output = truncate(text, max_lines, max_bytes),
    is_error = is_error or false,
    body = view(ui_text, is_error, ctx),
    state = { text = ui_text },
  }
end

local function search_or_rewrite(rewrite)
  return function(input, ctx, operation)
    validate(input, rewrite)
    if rewrite and input.dry_run == false then
      local session, err = maki.session.read({ session = ctx:session_id() })
      if not session then error("Cannot check Maki session mode: " .. tostring(err), 0) end
      if session.mode == "plan" then error("Cannot apply ast-grep rewrites in plan mode; use dry_run=true", 0) end
    end
    local matches = matches_from(run(argv(input, rewrite, false), operation), rewrite)
    if #matches == 0 then return "No matches found. No files changed." end
    if not rewrite or input.dry_run ~= false then
      local summary = rewrite and ("Preview: " .. #matches .. " matches. No files changed. Set dry_run=false to apply.")
        or ("Found " .. #matches .. " matches:")
      local limit = ctx:output_mode() == "full" and #matches or 100
      return summary .. "\n\n" .. format_matches(matches, rewrite, limit),
        summary .. "\n\n" .. format_matches(matches, rewrite, 1000)
    end
    operation.applying = true
    local result = run(argv(input, true, true), operation)
    local report = trim(result.stdout .. "\n" .. result.stderr)
    -- Another writer can remove every match after our preflight. ast-grep
    -- reports that no-op with exit 1 and no output, rather than an apply count.
    if result.exit_code == 1 and report == "" then
      operation.applying = false
      return "No matches remain to apply. ast-grep made no changes."
    end
    if result.exit_code ~= 0 then
      error("ast-grep apply failed (exit " .. tostring(result.exit_code) .. "):\n" .. diagnostics(result), 0)
    end
    if not report:match("^Applied %d+ changes$") then
      error("Unexpected ast-grep apply report; could not confirm completion:\n" .. truncate(report, nil, 8000), 0)
    end
    return report .. "\nRun search or inspect the diff to verify the resulting code."
  end
end

-- ast-grep prints the tree to stderr before compiling the pattern, so a
-- multi-statement snippet shows its tree followed by the pattern error.
local function syntax_tree(input, _ctx, operation)
  validate_tree(input)
  local result = run(tree_argv(input), operation)
  local tree = trim(result.stderr)
  if trim(result.stdout) ~= "" or not tree:match("^Debug ") then
    error("ast-grep syntax tree failed (exit " .. tostring(result.exit_code) .. "):\n" .. diagnostics(result), 0)
  end
  return tree
end

local function handler(body)
  return function(input, ctx)
    local operation = { applying = false }
    local permit = serial:acquire()
    local released = false
    local function release()
      if not released then released = true; permit:release() end
    end
    maki.async.on_cancel(function(reason)
      if operation.job then maki.fn.jobstop(operation.job) end
      release()
      ctx:finish({ llm_output = "ast-grep " .. reason .. (operation.applying and PARTIAL or "") .. "\n[output incomplete]", is_error = true })
    end)
    local ok, text, ui_text = pcall(body, input, ctx, operation)
    if operation.job then maki.fn.jobstop(operation.job) end
    release()
    if not ok then return response(tostring(text) .. (operation.applying and PARTIAL or ""), nil, true, ctx) end
    return response(text, ui_text, false, ctx)
  end
end

local function restore(_input, output, is_error, ctx)
  local saved = ctx:state()
  return view(saved and saved.text or output, is_error, ctx)
end

local function render_header(label, text, detail)
  local buf = maki.ui.buf()
  buf:line({ { label, "dim" }, { truncate((text or ""):gsub("[\r\n]", " "), 1, 160), "tool" }, { " (" .. detail .. ")", "dim" } })
  return buf
end

-- Maki normally coerces parameter types and drops unknown keys. Validate
-- the original call first so e.g. dry_run="false" cannot turn into a write.
local function register(spec, check)
  maki.api.set_slot("tool." .. spec.name .. ".input", function(prev, input, ctx)
    local ok, err = pcall(check, input)
    if not ok then return nil, tostring(err) end
    return prev(input, ctx)
  end)
  spec.permission = "run"
  spec.restore = restore
  maki.api.register_tool(spec)
end

for _, rewrite in ipairs({ false, true }) do
  register({
    name = rewrite and "ast_grep_rewrite" or "ast_grep_search",
    kind = rewrite and "edit" or "search",
    permission_scopes = rewrite and function(input)
      if input.dry_run ~= false then return { scopes = { "ast-grep preview" } } end
      return { scopes = { "ast-grep rewrite " .. table.concat(input.paths or { "." }, ", ") } }
    end or nil,
    description = rewrite
      and "Preview or apply an AST rewrite. Defaults to dry_run=true without writing. Review the preview, then repeat with dry_run=false to apply ALL matches in the selected paths/globs. Uses syntax, not symbol/type resolution. Reruns against current files; no rollback. Inspect the diff after applying or an apply error."
      or "Find code by syntax tree, including multiline calls. Example: pattern oldApi($$$ARGS), language cpp. Returns locations and matched code. Read-only. Narrow paths/globs for large results.",
    schema = { type = "object", properties = rewrite and rewrite_properties or properties },
    header = function(input)
      local label = rewrite and (input.dry_run == false and "apply " or "preview ") or "search "
      return render_header(label, input.pattern, input.language or "")
    end,
    handler = handler(search_or_rewrite(rewrite)),
  }, function(input) validate(input, rewrite) end)
end

register({
  name = "ast_grep_syntax_tree",
  kind = "search",
  description = "Show how ast-grep parses a pattern or code snippet, without searching files. When a search unexpectedly matches nothing, compare the pattern (format pattern) with a snippet of the target code (format ast or cst) and adjust the pattern to the target's node kinds. ERROR nodes mean the code does not parse as written.",
  schema = { type = "object", properties = tree_properties },
  header = function(input)
    return render_header("tree ", input.code, (input.language or "") .. ", " .. (input.format or "pattern"))
  end,
  handler = handler(syntax_tree),
}, validate_tree)

-- An empty scopes list forces a prompt in Maki. Give read-only previews their
-- own allow rule; explicit user denies still win and apply uses another scope.
maki.api.register_permission_rule({ tool = "ast_grep_rewrite", scope = "ast-grep preview" })

maki.api.register_prompt_hint({
  slot = "tool_usage",
  content = "- Use **ast_grep_search** and **ast_grep_rewrite** rather than ast-grep in bash when code structure matters (calls, definitions, multiline expressions); preview a rewrite before repeating it with dry_run=false. When a pattern unexpectedly matches nothing, check it with **ast_grep_syntax_tree**.",
})

-- Optional, from a custom package loader: require("ast_grep.init").setup({ timeout_secs = 120 })
local function setup(opts)
  if type(opts) ~= "table" then error("ast_grep.setup expects a table", 2) end
  for key, value in pairs(opts) do
    if key ~= "timeout_secs" then error("ast_grep.setup: unknown option " .. tostring(key), 2) end
    if type(value) ~= "number" or value % 1 ~= 0 or value < 1 then
      error("ast_grep.setup: timeout_secs must be a positive integer", 2)
    end
    options.timeout_ms = value * 1000
  end
end

return { setup = setup }
