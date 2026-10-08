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

// Exercise converter.ts's actual argv construction, stubbing only spawn.
const compiledConverter = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/main/converter.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function converterHarness() {
  const spawnCalls = []
  const electron = { app: { isPackaged: false } }
  const spawn = (cmd, args, options) => {
    spawnCalls.push({ cmd, args, options })
    return Object.assign(new EventEmitter(), { stdout: new EventEmitter(), stderr: new EventEmitter() })
  }
  const sandbox = {
    exports: {}, __dirname: path.join(__dirname, '../src/main'), process,
    require: (name) => name === 'electron' ? electron : name === 'node:child_process' ? { spawn } : require(name),
  }
  vm.runInNewContext(compiledConverter, sandbox)
  return { spawnCalls, converter: sandbox.exports }
}

function flagValues(args, flag) {
  const values = []
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === flag) values.push(args[i + 1])
  }
  return values
}

function harness(t, options = {}) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'logic2ableton-ipc-'))
  const handlers = new Map()
  const jobs = []
  const events = []
  const opened = []
  const appHandlers = new Map()
  const timers = new Map()
  const treeKills = []
  const windows = []
  let quitCalls = 0
  let destroyed = false
  let failStart = false
  const electron = {
    app: {
      whenReady: () => ({ then() {} }), on: (name, callback) => appHandlers.set(name, callback),
      getPath: () => scratch, quit: () => { quitCalls += 1 },
      requestSingleInstanceLock: () => options.ownsInstance !== false,
      isReady: () => true,
    },
    BrowserWindow: class extends EventEmitter {
      constructor() {
        super()
        this.destroyed = false
        this.webContents = { setWindowOpenHandler() {}, on() {} }
        windows.push(this)
      }
      loadFile() {}
      isDestroyed() { return this.destroyed }
      isMinimized() { assert.equal(this.destroyed, false); return false }
      show() { assert.equal(this.destroyed, false) }
      focus() { assert.equal(this.destroyed, false) }
      close() { this.destroyed = true; this.emit('closed') }
    },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    shell: {
      showItemInFolder: (file) => opened.push(file),
      openPath: async (file) => { opened.push(file); return '' },
    },
  }
  const calls = []
  const converter = {
    CONVERSION_DIRECTIONS: ['logic2ableton', 'ableton2logic', 'protools2ableton', 'protools2logic', 'ableton2protools', 'logic2protools'],
    runConversion(request, progress, error, onExit) {
      calls.push(request)
      if (failStart) { failStart = false; error('missing converter'); onExit(1); return null }
      const child = Object.assign(new EventEmitter(), {
        pid: 12345,
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
    exports: {}, __dirname: scratch, process: { ...process, platform: options.platform || 'linux' },
    setTimeout: (callback, delay) => {
      const timer = { unref() {} }
      timers.set(timer, { callback, delay })
      return timer
    },
    clearTimeout: (timer) => timers.delete(timer),
    require: (name) => name === 'electron' ? electron : name === './converter' ? converter
      : name === 'node:child_process' ? { execFile: (file, args, options, callback) => treeKills.push({ file, args, options, callback }) }
      : require(name),
  })
  const event = { sender: { isDestroyed: () => destroyed, send: (channel, data) => events.push({ channel, data }) } }
  t.after(() => {
    for (const job of jobs) job.exit(0)
    fs.rmSync(scratch, { recursive: true, force: true })
  })
  return {
    jobs, events, opened, scratch, calls, treeKills, windows,
    preview: (overrides = {}) => handlers.get('start-preview')(
      event, { direction: 'protools2ableton', sourcePath: 'Session.ptx', outputDir: '', reportOnly: true, tempo: 120, ...overrides },
    ),
    convert: (overrides = {}) => handlers.get('start-conversion')(
      event, { direction: 'logic2ableton', sourcePath: 'Song.logicx', outputDir: path.join(scratch, 'out'), reportOnly: false, ...overrides },
    ),
    cancel: () => handlers.get('cancel-active-job')(),
    reveal: (file) => handlers.get('show-in-folder')({}, file),
    open: (file) => handlers.get('open-file')({}, file),
    getHistory: () => handlers.get('get-history')(),
    addHistory: (record) => handlers.get('add-history')({}, record),
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
    closeAllWindows: () => appHandlers.get('window-all-closed')(),
    secondInstance: () => appHandlers.get('second-instance')(),
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

test('preview error progress forwards failure_stage, error, and report unchanged', async (t) => {
  // The 2026-09-16 review found the desktop hiding the specific exception
  // behind a preferred report and sending setting-derived failures (e.g. a
  // malformed --timeline file) to the terminal error screen. Both fixes rely
  // on these CLI-emitted fields reaching the renderer's preview-progress
  // listener untouched; this locks in that the main process doesn't strip or
  // reorder them on the way through.
  const h = harness(t)
  await h.preview()
  h.jobs[0].progress({
    stage: 'error',
    progress: 0.2,
    message: 'Failed during timeline: Expecting property name enclosed in double quotes.',
    failure_stage: 'timeline',
    error: 'Expecting property name enclosed in double quotes.',
    report: 'CONVERSION FAILED\n  Stage: timeline\n  Error: Expecting property name enclosed in double quotes.\n',
  })
  h.jobs[0].exit(1)
  const event = h.events.find((entry) => entry.channel === 'preview-progress')
  assert.equal(event.data.failure_stage, 'timeline')
  assert.equal(event.data.error, 'Expecting property name enclosed in double quotes.')
  assert.match(event.data.report, /Stage: timeline/)
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

test('conversion history round-trips MIDI track and note counts', async (t) => {
  const h = harness(t)
  const record = {
    id: 'abc123',
    direction: 'logic2ableton',
    projectName: 'Song',
    inputPath: 'Song.logicx',
    outputPath: path.join(h.scratch, 'out', 'Song.als'),
    date: new Date().toISOString(),
    status: 'success',
    report: 'report text',
    stats: { tracks: 0, clips: 0, audioFiles: 0, midiTracks: 1, midiNotes: 4 },
  }

  const afterAdd = await h.addHistory(record)
  assert.equal(afterAdd[0].stats.midiTracks, 1)
  assert.equal(afterAdd[0].stats.midiNotes, 4)

  const history = await h.getHistory()
  assert.equal(history[0].stats.midiTracks, 1)
  assert.equal(history[0].stats.midiNotes, 4)
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
  await new Promise(setImmediate)
  assert.equal(h.quitCalls(), 1)
})

test('Windows cancellation waits for process-tree termination after the bootloader exits', async (t) => {
  const h = harness(t, { platform: 'win32' })
  await h.preview()
  let done = false
  const stopping = h.cancel().then(() => { done = true })
  assert.equal(path.basename(h.treeKills[0].file), 'taskkill.exe')
  assert.deepEqual(Array.from(h.treeKills[0].args), ['/PID', '12345', '/T', '/F'])
  assert.equal(h.treeKills[0].options.windowsHide, true)
  h.jobs[0].exit(1)
  await new Promise(setImmediate)
  assert.equal(done, false)
  await assert.rejects(h.preview(), /already in progress/)
  h.treeKills[0].callback(null)
  await stopping
  await h.preview()
})

test('Windows cancellation accepts a converter that already finished on its own', async (t) => {
  const h = harness(t, { platform: 'win32' })
  await h.preview()
  const stopping = h.cancel()
  h.jobs[0].exit(0)
  h.treeKills[0].callback(Object.assign(new Error('The process "12345" not found.'), { code: 128 }))
  await stopping
  await h.preview()
})

test('Windows tree termination failure keeps the job reserved but still lets the app quit', async (t) => {
  const h = harness(t, { platform: 'win32' })
  await h.preview()
  const stopping = h.cancel()
  h.jobs[0].exit(1)
  h.treeKills[0].callback(Object.assign(new Error('access denied'), { code: 1 }))
  await assert.rejects(stopping, /process tree/)
  await assert.rejects(h.preview(), /already in progress/)
  await assert.rejects(h.cancel(), /worker may still be running/)
  assert.equal(h.treeKills.length, 1)
  assert.equal(h.requestQuit(), true)
  await new Promise(setImmediate)
  assert.equal(h.quitCalls(), 1)
  assert.equal(h.requestQuit(), false)
})

test('a second application instance quits before opening shared history', (t) => {
  const h = harness(t, { ownsInstance: false })
  assert.equal(h.quitCalls(), 1)
})

test('closing the last macOS window enters the guarded quit lifecycle', (t) => {
  const h = harness(t, { platform: 'darwin' })
  h.closeAllWindows()
  assert.equal(h.quitCalls(), 1)
})

test('a second instance restores a window after the previous window was destroyed', (t) => {
  const h = harness(t)
  h.secondInstance()
  h.windows[0].close()
  h.secondInstance()
  assert.equal(h.windows.length, 2)
  assert.equal(h.windows[1].isDestroyed(), false)
  h.secondInstance()
  assert.equal(h.windows.length, 2)
})

test('a second instance does not open a window while the app is quitting', async (t) => {
  const h = harness(t)
  h.secondInstance()
  await h.preview()
  assert.equal(h.requestQuit(), true)
  h.windows[0].close()
  h.secondInstance()
  assert.equal(h.windows.length, 1)
})

test('corrupt history is kept beside a new history that later conversions save to', async (t) => {
  const h = harness(t)
  const file = path.join(h.scratch, 'conversion-history.json')
  fs.writeFileSync(file, '{broken')
  const record = { id: 'a', direction: 'logic2ableton', projectName: 'Song', inputPath: 'Song.logicx',
    outputPath: 'Song.als', date: new Date().toISOString(), status: 'success', report: '' }
  await assert.rejects(h.getHistory(), /kept as conversion-history\.unreadable-\d+\.json and a new history was started/)
  const kept = fs.readdirSync(h.scratch).filter(name => name.startsWith('conversion-history.unreadable-'))
  assert.equal(kept.length, 1)
  assert.equal(fs.readFileSync(path.join(h.scratch, kept[0]), 'utf8'), '{broken')
  assert.deepEqual(Array.from(await h.addHistory(record), entry => entry.id), ['a'])
  assert.deepEqual(Array.from(await h.getHistory(), entry => entry.id), ['a'])
})

test('a history that is not a list is kept aside when a save finds it first', async (t) => {
  const h = harness(t)
  fs.writeFileSync(path.join(h.scratch, 'conversion-history.json'), '{"records": []}')
  const record = { id: 'b', direction: 'logic2ableton', projectName: 'Song', inputPath: 'Song.logicx',
    outputPath: 'Song.als', date: new Date().toISOString(), status: 'success', report: '' }
  assert.deepEqual(Array.from(await h.addHistory(record), entry => entry.id), ['b'])
  assert.equal(fs.readdirSync(h.scratch).filter(name => name.startsWith('conversion-history.unreadable-')).length, 1)
})

test('an invalid history output does not hide valid records', async (t) => {
  const h = harness(t)
  const valid = { id: 'good', direction: 'logic2ableton', projectName: 'Song', inputPath: 'Song.logicx',
    outputPath: 'Song.als', date: new Date().toISOString(), status: 'success', report: '' }
  fs.writeFileSync(path.join(h.scratch, 'conversion-history.json'), JSON.stringify([valid, { ...valid, id: 'bad', outputPath: ' ' }]))
  const history = await h.getHistory()
  assert.deepEqual(Array.from(history, record => record.id), ['good'])
})

test('forwards smpte start, keep-unwarped, and timeline for logic2ableton', async (t) => {
  const h = harness(t)
  const timelinePath = path.join(h.scratch, 'timeline.json')
  await h.convert({
    smpteStart: 'auto',
    keepUnwarped: ['LTC*', ' Pilot* ', ''],
    timelinePath,
  })
  assert.equal(h.calls[0].direction, 'logic2ableton')
  assert.equal(h.calls[0].smpteStart, 'auto')
  assert.deepEqual(h.calls[0].keepUnwarped, ['LTC*', 'Pilot*'])
  assert.equal(h.calls[0].timelinePath, path.normalize(timelinePath))
})

test('omits logic-only options for non-logic-source directions', async (t) => {
  const h = harness(t)
  await h.convert({
    direction: 'ableton2logic',
    sourcePath: 'Set.als',
    smpteStart: 'auto',
    keepUnwarped: ['LTC*'],
    timelinePath: path.join(h.scratch, 'timeline.json'),
  })
  assert.equal(h.calls[0].smpteStart, undefined)
  assert.equal(h.calls[0].keepUnwarped, undefined)
  assert.equal(h.calls[0].timelinePath, undefined)
  h.jobs[0].exit(0)

  await h.convert({ direction: 'protools2ableton', sourcePath: 'Session.ptx', tempo: 120, smpteStart: '01:00:00:00' })
  assert.equal(h.calls[1].smpteStart, undefined)
  assert.equal(h.calls[1].tempo, 120)
})

test('omits keep-unwarped and timeline for logic2protools', async (t) => {
  const h = harness(t)
  await h.convert({
    direction: 'logic2protools',
    smpteStart: 'auto',
    keepUnwarped: ['LTC*'],
    timelinePath: path.join(h.scratch, 'timeline.json'),
  })
  assert.equal(h.calls[0].smpteStart, 'auto')
  assert.equal(h.calls[0].keepUnwarped, undefined)
  assert.equal(h.calls[0].timelinePath, undefined)
})

test('rejects an invalid SMPTE start string and a control-character keep-unwarped pattern', async (t) => {
  const h = harness(t)
  await assert.rejects(h.convert({ smpteStart: 'not-a-time' }), /SMPTE start/)
  await assert.rejects(h.convert({ keepUnwarped: 'LTC*\x07' }), /control character/)
  assert.equal(h.calls.length, 0)
})

test('rejects SMPTE timecodes that are syntactically shaped but out of range', async (t) => {
  const h = harness(t)
  // Frame 30 is not < the CLI's fixed 30fps default.
  await assert.rejects(h.convert({ smpteStart: '01:00:00:30' }), /SMPTE start/)
  // Minutes must be 00-59.
  await assert.rejects(h.convert({ smpteStart: '01:60:00:00' }), /SMPTE start/)
  // Seconds must be 00-59.
  await assert.rejects(h.convert({ smpteStart: '01:00:60:00' }), /SMPTE start/)
  assert.equal(h.calls.length, 0)

  await h.convert({ smpteStart: '01:00:00:29' })
  assert.equal(h.calls[0].smpteStart, '01:00:00:29')
})

test('rejects a keep-unwarped pattern that starts with a dash', async (t) => {
  const h = harness(t)
  await assert.rejects(h.convert({ keepUnwarped: ['-x*'] }), /cannot start with/)
  assert.equal(h.calls.length, 0)
})

test('start-preview gates smpte start, keep-unwarped, and timeline by direction the same way start-conversion does', async (t) => {
  const h = harness(t)
  const timelinePath = path.join(h.scratch, 'timeline.json')
  await h.preview({
    direction: 'logic2ableton',
    sourcePath: 'Song.logicx',
    smpteStart: 'auto',
    keepUnwarped: ['LTC*', ' Pilot* ', ''],
    timelinePath,
  })
  assert.equal(h.calls[0].smpteStart, 'auto')
  assert.deepEqual(h.calls[0].keepUnwarped, ['LTC*', 'Pilot*'])
  assert.equal(h.calls[0].timelinePath, path.normalize(timelinePath))
  h.jobs[0].exit(0)

  await h.preview({
    direction: 'logic2protools',
    sourcePath: 'Song.logicx',
    smpteStart: 'auto',
    keepUnwarped: ['LTC*'],
    timelinePath,
  })
  assert.equal(h.calls[1].smpteStart, 'auto')
  assert.equal(h.calls[1].keepUnwarped, undefined)
  assert.equal(h.calls[1].timelinePath, undefined)
  h.jobs[1].exit(0)

  await h.preview({
    direction: 'ableton2logic',
    sourcePath: 'Set.als',
    smpteStart: 'auto',
    keepUnwarped: ['LTC*'],
    timelinePath,
  })
  assert.equal(h.calls[2].smpteStart, undefined)
  assert.equal(h.calls[2].keepUnwarped, undefined)
  assert.equal(h.calls[2].timelinePath, undefined)
})

test('converter.ts spawns the real argv with repeated --keep-unwarped flags, --smpte-start, and --timeline', () => {
  const h = converterHarness()
  const child = h.converter.runConversion(
    {
      direction: 'logic2ableton',
      sourcePath: 'Song.logicx',
      outputDir: 'out',
      reportOnly: false,
      smpteStart: 'auto',
      keepUnwarped: ['LTC*', 'Pilot*'],
      timelinePath: 'timeline.json',
    },
    () => {}, () => {}, () => {},
  )
  assert.notEqual(child, null)
  assert.equal(h.spawnCalls.length, 1)
  const { args } = h.spawnCalls[0]
  const modeIndex = args.indexOf('--mode')
  assert.equal(args[modeIndex + 1], 'logic2ableton')
  assert.deepEqual(flagValues(args, '--keep-unwarped'), ['LTC*', 'Pilot*'])
  assert.deepEqual(flagValues(args, '--smpte-start'), ['auto'])
  assert.deepEqual(flagValues(args, '--timeline'), ['timeline.json'])
  assert.ok(modeIndex < args.indexOf('--smpte-start'))
  assert.ok(args.indexOf('--smpte-start') < args.indexOf('--keep-unwarped'))
})

test('converter.ts omits --smpte-start and --timeline when the request leaves them unset', () => {
  const h = converterHarness()
  h.converter.runConversion(
    { direction: 'ableton2logic', sourcePath: 'Set.als', outputDir: 'out', reportOnly: false },
    () => {}, () => {}, () => {},
  )
  const { args } = h.spawnCalls[0]
  assert.equal(args.includes('--smpte-start'), false)
  assert.equal(args.includes('--keep-unwarped'), false)
  assert.equal(args.includes('--timeline'), false)
})

test('forwards keep-outputs for logic2ableton', async (t) => {
  const h = harness(t)
  await h.convert({ keepOutputs: true })
  assert.equal(h.calls[0].direction, 'logic2ableton')
  assert.equal(h.calls[0].keepOutputs, true)
})

test('omits keep-outputs when false or unset', async (t) => {
  const h = harness(t)
  await h.convert({ keepOutputs: false })
  assert.equal(h.calls[0].keepOutputs, undefined)
  h.jobs[0].exit(0)

  await h.convert({})
  assert.equal(h.calls[1].keepOutputs, undefined)
})

test('drops keep-outputs for directions other than logic2ableton, for both start-conversion and start-preview', async (t) => {
  const h = harness(t)
  await h.convert({ direction: 'logic2protools', sourcePath: 'Song.logicx', keepOutputs: true })
  assert.equal(h.calls[0].keepOutputs, undefined)
  h.jobs[0].exit(0)

  await h.convert({ direction: 'ableton2logic', sourcePath: 'Set.als', keepOutputs: true })
  assert.equal(h.calls[1].keepOutputs, undefined)
  h.jobs[1].exit(0)

  await h.preview({ direction: 'logic2ableton', sourcePath: 'Song.logicx', keepOutputs: true })
  assert.equal(h.calls[2].keepOutputs, true)
  h.jobs[2].exit(0)

  await h.preview({ direction: 'protools2ableton', sourcePath: 'Session.ptx', tempo: 120, keepOutputs: true })
  assert.equal(h.calls[3].keepOutputs, undefined)
})

test('rejects a non-boolean keep-outputs value', async (t) => {
  const h = harness(t)
  await assert.rejects(h.convert({ keepOutputs: 'yes' }), /Keep-outputs must be true or false/)
  await assert.rejects(h.convert({ keepOutputs: 1 }), /Keep-outputs must be true or false/)
  assert.equal(h.calls.length, 0)
})

test('converter.ts spawns --keep-outputs when the request sets it, and omits it when false or unset', () => {
  const h = converterHarness()
  h.converter.runConversion(
    { direction: 'logic2ableton', sourcePath: 'Song.logicx', outputDir: 'out', reportOnly: false, keepOutputs: true },
    () => {}, () => {}, () => {},
  )
  assert.equal(h.spawnCalls[0].args.includes('--keep-outputs'), true)

  h.converter.runConversion(
    { direction: 'logic2ableton', sourcePath: 'Song.logicx', outputDir: 'out', reportOnly: false, keepOutputs: false },
    () => {}, () => {}, () => {},
  )
  assert.equal(h.spawnCalls[1].args.includes('--keep-outputs'), false)

  h.converter.runConversion(
    { direction: 'ableton2logic', sourcePath: 'Set.als', outputDir: 'out', reportOnly: false },
    () => {}, () => {}, () => {},
  )
  assert.equal(h.spawnCalls[2].args.includes('--keep-outputs'), false)
})
