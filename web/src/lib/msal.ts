import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type IPublicClientApplication,
} from '@azure/msal-browser'
import type { TokenProvider } from './auth'

export class MsalTokenProvider implements TokenProvider {
  private app: IPublicClientApplication
  private scope: string
  private redirectStarted = false

  constructor(app: IPublicClientApplication, scope: string) {
    this.app = app
    this.scope = scope
  }

  async getToken(): Promise<string> {
    if (this.redirectStarted) throw new Error('Sign-in redirect started')
    const scopes = [this.scope]
    const account = this.app.getActiveAccount()
    if (!account) {
      return this.redirect(() => this.app.loginRedirect({ scopes }))
    }
    try {
      return (await this.app.acquireTokenSilent({ scopes, account })).accessToken
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) {
        return this.redirect(() => this.app.acquireTokenRedirect({ scopes }))
      }
      throw error
    }
  }

  private async redirect(start: () => Promise<void>): Promise<never> {
    if (this.redirectStarted) throw new Error('Sign-in redirect started')
    this.redirectStarted = true
    await start()
    throw new Error('Sign-in redirect started')
  }
}

export async function createMsalProvider(env: ImportMetaEnv): Promise<MsalTokenProvider | null> {
  if (!env.VITE_ENTRA_CLIENT_ID) return null
  if (!env.VITE_ENTRA_TENANT_ID) {
    throw new Error('VITE_ENTRA_TENANT_ID is required when VITE_ENTRA_CLIENT_ID is set')
  }
  const app = new PublicClientApplication({
    auth: {
      clientId: env.VITE_ENTRA_CLIENT_ID,
      authority: `https://login.microsoftonline.com/${env.VITE_ENTRA_TENANT_ID}`,
      redirectUri: window.location.origin + '/',
    },
    cache: { cacheLocation: 'localStorage' },
  })
  await app.initialize()
  let redirectAccount
  try {
    redirectAccount = (await app.handleRedirectPromise())?.account
  } catch (error) {
    const errorCode = error && typeof error === 'object' && 'errorCode' in error
      && typeof error.errorCode === 'string' ? error.errorCode : undefined
    console.warn(errorCode)
  }
  const account = redirectAccount ?? app.getActiveAccount() ?? app.getAllAccounts()[0]
  if (account) app.setActiveAccount(account)
  return new MsalTokenProvider(app, env.VITE_API_SCOPE || 'api://foodcoach-prod/Food.ReadWrite')
}
