import assert from "node:assert/strict"
import * as childProcess from "node:child_process"
import * as fs from "node:fs"
import * as path from "node:path"
import { tmpdir } from "node:os"
import { fileURLToPath } from "node:url"
import vm from "node:vm"
import { test } from "node:test"
import ts from "typescript"

test("Windows cancellation stops a real parent and worker", { skip: process.platform !== "win32", timeout: 15000 }, async (t) => {
  const scratch = fs.mkdtempSync(path.join(tmpdir(), "logic2ableton-process-tree-"))
  const marker = path.join(scratch, "worker-survived.txt")
  const source = fs.readFileSync(new URL("../src/main/index.ts", import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const dependencies = {
    "node:child_process": childProcess, "node:fs": fs, "node:path": path,
    electron: {
      app: { requestSingleInstanceLock: () => true, whenReady: () => ({ then() {} }), on() {} },
      ipcMain: { handle() {} },
    },
    "./converter": { CONVERSION_DIRECTIONS: [] },
  }
  const sandbox = {
    exports: {}, process, setTimeout, clearTimeout,
    __dirname: fileURLToPath(new URL("../src/main", import.meta.url)),
    require: (name) => {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`)
      return dependencies[name]
    },
  }
  vm.runInNewContext(compiled, sandbox)
  const workerCode = `import { writeFileSync } from 'node:fs'; console.log(process.pid); setTimeout(() => writeFileSync(${JSON.stringify(marker)}, 'survived'), 1500); setInterval(() => {}, 1000)`
  const parentCode = `import { spawn } from 'node:child_process'; spawn(process.execPath, ['--input-type=module', '-e', ${JSON.stringify(workerCode)}], { stdio: ['ignore', 'inherit', 'inherit'], windowsHide: true }); setInterval(() => {}, 1000)`
  const parent = childProcess.spawn(process.execPath, ["--input-type=module", "-e", parentCode], { windowsHide: true })
  let workerPid
  t.after(() => {
    // Only the two processes created by this test are cleanup targets.
    for (const pid of [workerPid, parent.pid]) {
      if (pid) { try { process.kill(pid) } catch { /* Already terminated. */ } }
    }
    fs.rmSync(scratch, { recursive: true, force: true })
  })
  workerPid = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Worker did not start")), 5000)
    let output = ""
    parent.once("error", reject)
    parent.stdout.on("data", (data) => {
      output += data
      if (output.includes("\n")) { clearTimeout(timer); resolve(Number(output.trim())) }
    })
  })
  assert.ok(Number.isSafeInteger(workerPid) && workerPid > 0)
  await sandbox.terminateProcessTree(parent)
  await new Promise(resolve => setTimeout(resolve, 1700))
  assert.equal(fs.existsSync(marker), false, "Worker must not write after cancellation")
  for (const pid of [parent.pid, workerPid]) {
    assert.throws(() => process.kill(pid, 0), { code: "ESRCH" }, `Process ${pid} must have exited`)
  }
  const finishing = childProcess.spawn(process.execPath, ["-e", "process.exit(0)"], { windowsHide: true })
  t.after(() => { try { finishing.kill() } catch { /* Already exited. */ } })
  await new Promise(resolve => finishing.once("exit", resolve))
  await sandbox.terminateProcessTree(finishing)
  assert.equal(finishing.exitCode, 0, "A converter that already finished must not be reported as a failed stop")
})
