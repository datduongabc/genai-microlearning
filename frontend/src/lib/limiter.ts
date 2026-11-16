// Runs at most `size` tasks at once, in the order they were asked for. A task whose caller has
// gone by the time its turn comes is skipped instead of run.
export interface Limiter {
  run<T>(task: () => Promise<T>, gone?: () => boolean): Promise<T | undefined>
}

export function createLimiter(size: number): Limiter {
  let active = 0
  const waiting: (() => void)[] = []
  const next = () => {
    if (active >= size) return
    const start = waiting.shift()
    if (start) start()
  }
  return {
    run(task, gone) {
      return new Promise((resolve, reject) => {
        waiting.push(() => {
          if (gone?.()) {
            resolve(undefined)
            next()
            return
          }
          active++
          task().then(resolve, reject).finally(() => {
            active--
            next()
          })
        })
        next()
      })
    },
  }
}
