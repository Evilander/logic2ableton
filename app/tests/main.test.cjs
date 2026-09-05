const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const vm = require('node:vm')
const { EventEmitter } = require('node:events')
const { test } = require('node:test')
const ts = require('typescript')

// Exercise the actual IPC handlers with controlled child-process completion.
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/main/index.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function harness(t) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'logic2ableton-ipc-'))
  const handlers = new Map()
  const jobs = []
  const events = []
  const opened = []
  const appHandlers = new Map()
  const timers = new Map()
  let quitCalls = 0
  let destroyed = false
  let failStart = false
  const electron = {
    app: {
      whenReady: () => ({ then() {} }), on: (name, callback) => appHandlers.set(name, callback),
      getPath: () => scratch, quit: () => { quitCalls += 1 },
    },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    shell: {
      showItemInFolder: (file) => opened.push(file),
      openPath: async (file) => { opened.push(file); return '' },
    },
  }
  const converter = {
    CONVERSION_DIRECTIONS: ['logic2ableton', 'ableton2logic', 'protools2ableton', 'protools2logic', 'ableton2protools', 'logic2protools'],
    runConversion(direction, source, output, progress, error, onExit) {
      if (failStart) { failStart = false; error('missing converter'); onExit(1); return null }
      const child = Object.assign(new EventEmitter(), {
        killed: false, exitCode: null, signalCode: null, signals: [],
        kill(signal = 'SIGTERM') { this.killed = true; this.signals.push(signal); return true },
      })
      const job = { child, progress, error, closed: false, exit(code) {
        if (this.closed) return
        this.closed = true
        child.exitCode = code
        child.emit('exit', code)
        onExit(code)
      } }
      jobs.push(job)
      return child
    },
  }
  vm.runInNewContext(compiled, {
    exports: {}, __dirname: scratch, process,
    setTimeout: (callback, delay) => {
      const timer = { unref() {} }
      timers.set(timer, { callback, delay })
      return timer
    },
    clearTimeout: (timer) => timers.delete(timer),
    require: (name) => name === 'electron' ? electron : name === './converter' ? converter : require(name),
  })
  const event = { sender: { isDestroyed: () => destroyed, send: (channel, data) => events.push({ channel, data }) } }
  t.after(() => {
    for (const job of jobs) job.exit(0)
    fs.rmSync(scratch, { recursive: true, force: true })
  })
  return {
    jobs, events, opened, scratch,
    preview: () => handlers.get('start-preview')(event, 'protools2ableton', 'Session.ptx', 120),
    cancel: () => handlers.get('cancel-active-job')(),
    reveal: (file) => handlers.get('show-in-folder')({}, file),
    open: (file) => handlers.get('open-file')({}, file),
    destroy: () => { destroyed = true },
    failNextStart: () => { failStart = true },
    runTimers: (delay) => {
      for (const [timer, item] of timers) {
        if (item.delay === delay) { timers.delete(timer); item.callback() }
      }
    },
    requestQuit: () => {
      let prevented = false
      appHandlers.get('before-quit')({ preventDefault: () => { prevented = true } })
      return prevented
    },
    quitCalls: () => quitCalls,
  }
}

test('cancellation waits for child exit and suppresses all canceled job events', async (t) => {
  const h = harness(t)
  await h.preview()
  let cancelled = false
  const cancellation = h.cancel().then(() => { cancelled = true })
  await Promise.resolve()
  assert.equal(cancelled, false)
  assert.equal(h.jobs[0].child.killed, true)
  await assert.rejects(h.preview(), /already in progress/)
  h.jobs[0].progress({ stage: 'complete' })
  h.jobs[0].error('old error')
  h.jobs[0].exit(1)
  await cancellation
  assert.equal(cancelled, true)
  assert.deepEqual(h.events, [])
  await h.preview()
  h.jobs[1].progress({ stage: 'complete' })
  h.jobs[1].exit(0)
  assert.deepEqual(h.events.map((event) => event.channel), ['preview-progress', 'preview-exit'])
})

test('two cancellation requests both wait for the same child', async (t) => {
  const h = harness(t)
  await h.preview()
  const first = h.cancel()
  const second = h.cancel()
  h.jobs[0].exit(1)
  await Promise.all([first, second])
  await h.preview()
  assert.equal(h.jobs.length, 2)
})

test('dotted approved directories can be revealed and opened', async (t) => {
  const h = harness(t)
  const directory = path.join(h.scratch, 'Song.v2 Logic Transfer')
  fs.mkdirSync(directory)
  await h.preview()
  h.jobs[0].progress({ stage: 'complete', package_path: directory })
  await h.reveal(directory)
  await h.open(directory)
  assert.deepEqual(h.opened, [directory, directory])
})

test('file opening keeps approval and extension restrictions', async (t) => {
  const h = harness(t)
  const executable = path.join(h.scratch, 'program.exe')
  fs.writeFileSync(executable, '')
  await assert.rejects(h.reveal(executable), /not available/)
  await h.preview()
  h.jobs[0].progress({ stage: 'complete', artifact_path: executable })
  await assert.rejects(h.open(executable), /Unsupported file type/)
  await h.reveal(executable)
  assert.deepEqual(h.opened, [executable])
})

test('a synchronous startup failure releases the job reservation', async (t) => {
  const h = harness(t)
  h.failNextStart()
  await h.preview()
  assert.deepEqual(h.events.map((event) => event.channel), ['preview-error', 'preview-exit'])
  await h.preview()
  assert.equal(h.jobs.length, 1)
})

test('closed renderer does not receive process events', async (t) => {
  const h = harness(t)
  await h.preview()
  h.destroy()
  h.jobs[0].error('late error')
  h.jobs[0].progress({ stage: 'complete' })
  h.jobs[0].exit(0)
  assert.deepEqual(h.events, [])
})

test('failed cancellation times out without releasing a running child', async (t) => {
  const h = harness(t)
  await h.preview()
  const cancellation = h.cancel()
  const rejected = assert.rejects(cancellation, /did not stop/)
  h.runTimers(5000)
  assert.deepEqual(h.jobs[0].child.signals, ['SIGTERM', 'SIGKILL'])
  h.runTimers(10000)
  await rejected
  await assert.rejects(h.preview(), /already in progress/)
  h.jobs[0].exit(1)
  await h.preview()
})

test('quit and repeated quit requests wait for converter termination', async (t) => {
  const h = harness(t)
  await h.preview()
  assert.equal(h.requestQuit(), true)
  assert.equal(h.requestQuit(), true)
  assert.equal(h.quitCalls(), 0)
  h.jobs[0].exit(1)
  await Promise.resolve()
  assert.equal(h.quitCalls(), 1)
})
