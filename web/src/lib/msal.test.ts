import { describe, expect, it, vi } from 'vitest'
import { InteractionRequiredAuthError, type IPublicClientApplication } from '@azure/msal-browser'
import { createMsalProvider, MsalTokenProvider } from './msal'

const scope = 'api://foodcoach-prod/Food.ReadWrite'

const { createApp } = vi.hoisted(() => ({ createApp: vi.fn() }))
vi.mock('@azure/msal-browser', async (importOriginal) => ({
  ...await importOriginal<typeof import('@azure/msal-browser')>(),
  PublicClientApplication: createApp,
}))

function fakeApp() {
  const account = { homeAccountId: 'user' }
  const methods = {
    getActiveAccount: vi.fn<() => typeof account | null>(() => account),
    acquireTokenSilent: vi.fn().mockResolvedValue({ accessToken: 'access-token' }),
    acquireTokenRedirect: vi.fn().mockResolvedValue(undefined),
    loginRedirect: vi.fn().mockResolvedValue(undefined),
    initialize: vi.fn().mockResolvedValue(undefined),
    handleRedirectPromise: vi.fn().mockResolvedValue(null),
    getAllAccounts: vi.fn(() => [account]),
    setActiveAccount: vi.fn(),
  }
  return { methods, account, app: methods as unknown as IPublicClientApplication }
}

describe('MsalTokenProvider', () => {
  it('getToken returns silent token', async () => {
    const { methods, account, app } = fakeApp()
    expect(await new MsalTokenProvider(app, scope).getToken()).toBe('access-token')
    expect(methods.acquireTokenSilent).toHaveBeenCalledWith({ scopes: [scope], account })
  })

  it('falls back to redirect on InteractionRequiredAuthError', async () => {
    const { methods, app } = fakeApp()
    methods.acquireTokenSilent.mockRejectedValue(new InteractionRequiredAuthError('interaction_required', 'Sign in'))
    await expect(new MsalTokenProvider(app, scope).getToken()).rejects.toThrow()
    expect(methods.acquireTokenRedirect).toHaveBeenCalledWith({ scopes: [scope] })
  })

  it('no account triggers loginRedirect', async () => {
    const { methods, app } = fakeApp()
    methods.getActiveAccount.mockReturnValue(null)
    await expect(new MsalTokenProvider(app, scope).getToken()).rejects.toThrow()
    expect(methods.loginRedirect).toHaveBeenCalledWith({ scopes: [scope] })
  })

  it('createMsalProvider returns null without client id', async () => {
    expect(await createMsalProvider({} as ImportMetaEnv)).toBeNull()
    expect(await createMsalProvider({ VITE_ENTRA_CLIENT_ID: '' } as ImportMetaEnv)).toBeNull()
  })

  it('empty scope falls back to the default', async () => {
    const { methods } = fakeApp()
    createApp.mockImplementation(function () { return methods })
    const provider = await createMsalProvider({
      VITE_ENTRA_CLIENT_ID: 'client', VITE_ENTRA_TENANT_ID: 'tenant', VITE_API_SCOPE: '',
    } as ImportMetaEnv)
    await provider!.getToken()
    expect(methods.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [scope], account: methods.getActiveAccount(),
    })
  })

  it('empty tenant with client id throws', async () => {
    await expect(createMsalProvider({
      VITE_ENTRA_CLIENT_ID: 'client', VITE_ENTRA_TENANT_ID: '',
    } as ImportMetaEnv)).rejects.toThrow(
      'VITE_ENTRA_TENANT_ID is required when VITE_ENTRA_CLIENT_ID is set',
    )
  })

  it('returns a provider after redirect failure and warns only the error code', async () => {
    const { methods } = fakeApp()
    methods.handleRedirectPromise.mockRejectedValue({
      errorCode: 'access_denied', message: 'sensitive token', accessToken: 'secret',
    })
    createApp.mockImplementation(function () { return methods })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const provider = await createMsalProvider({
        VITE_ENTRA_CLIENT_ID: 'client', VITE_ENTRA_TENANT_ID: 'tenant',
      } as ImportMetaEnv)
      expect(provider).toBeInstanceOf(MsalTokenProvider)
      expect(await provider!.getToken()).toBe('access-token')
      expect(warn).toHaveBeenCalledExactlyOnceWith('access_denied')
    } finally {
      warn.mockRestore()
    }
  })

  it.each(['login', 'token'])('starts only one pending %s redirect', async (kind) => {
    const { methods, app } = fakeApp()
    if (kind === 'login') methods.getActiveAccount.mockReturnValue(null)
    else methods.acquireTokenSilent.mockRejectedValue(
      new InteractionRequiredAuthError('interaction_required', 'Sign in'),
    )
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    const redirect = kind === 'login' ? methods.loginRedirect : methods.acquireTokenRedirect
    redirect.mockReturnValue(pending)
    const provider = new MsalTokenProvider(app, scope)
    const first = provider.getToken()
    await vi.waitFor(() => expect(redirect).toHaveBeenCalledTimes(1))
    const second = provider.getToken()
    const secondResult = expect(second).rejects.toThrow('Sign-in redirect started')
    finish()
    await secondResult
    await expect(first).rejects.toThrow('Sign-in redirect started')
    expect(redirect).toHaveBeenCalledTimes(1)
    expect(methods.getActiveAccount).toHaveBeenCalledTimes(1)
  })
})

describe('web bootstrap', () => {
  it.each(['', 'client'])('loads MSAL only with a non-empty client id (%s)', async (clientId) => {
    vi.resetModules()
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', clientId)
    const loadMsal = vi.fn()
    const createProvider = vi.fn().mockResolvedValue(null)
    const render = vi.fn()
    vi.doMock('./msal', () => {
      loadMsal()
      return { createMsalProvider: createProvider }
    })
    vi.doMock('react-dom/client', () => ({ createRoot: () => ({ render }) }))
    vi.doMock('../App', () => ({ default: () => null }))
    try {
      await import('../main')
      expect(loadMsal).toHaveBeenCalledTimes(clientId ? 1 : 0)
      expect(createProvider).toHaveBeenCalledTimes(clientId ? 1 : 0)
      expect(render).toHaveBeenCalledTimes(1)
    } finally {
      vi.doUnmock('./msal')
      vi.doUnmock('react-dom/client')
      vi.doUnmock('../App')
      vi.unstubAllEnvs()
      vi.resetModules()
    }
  })
})
