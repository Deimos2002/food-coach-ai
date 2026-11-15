export interface TokenProvider {
  getToken(): Promise<string>
}

export class DevTokenProvider implements TokenProvider {
  async getToken(): Promise<string> {
    // Read only in dev builds, so the dev token never ships in production.
    if (import.meta.env.DEV) {
      const token = import.meta.env.VITE_DEV_TOKEN
      if (token) return token
    }
    throw new Error('VITE_DEV_TOKEN missing')
  }
}

let provider: TokenProvider = new DevTokenProvider()

export function setTokenProvider(p: TokenProvider): void {
  provider = p
}

export function getTokenProvider(): TokenProvider {
  return provider
}
