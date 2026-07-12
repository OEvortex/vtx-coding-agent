# Security

agenite-claw includes built-in security features to protect against common attack vectors.

## SSRF Protection

The `network.py` module blocks requests to private/internal networks.

### Blocked Networks

| CIDR | Description |
|------|-------------|
| `0.0.0.0/8` | Current network |
| `10.0.0.0/8` | Private class A |
| `100.64.0.0/10` | Carrier-grade NAT |
| `127.0.0.0/8` | Loopback |
| `169.254.0.0/16` | Link-local / cloud metadata |
| `172.16.0.0/12` | Private class B |
| `192.168.0.0/16` | Private class C |
| `::1/128` | IPv6 loopback |
| `fc00::/7` | IPv6 unique local |
| `fe80::/10` | IPv6 link-local |

### Whitelist Bypass

For services running on private networks (e.g., Tailscale), add CIDRs to the whitelist:

```json
{
  "tools": {
    "ssrf_whitelist": ["100.64.0.0/10"]
  }
}
```

### DNS Resolution

`validate_url_target()` resolves the hostname and checks ALL resolved addresses against the blocklist. Only `http` and `https` schemes are allowed.

### Redirect Validation

`validate_resolved_url()` checks post-redirect URLs to prevent redirect-based SSRF attacks.

### Loopback Allowlist

When `allow_loopback=True`, literal `localhost` or `127.x.x.x` is permitted when ALL resolved addresses are loopback.

## Workspace Sandboxing

The `workspace_access.py` module controls file system access scope.

### Access Modes

| Mode | Description |
|------|-------------|
| `restricted` | Agent tools can only access files inside the workspace |
| `full` | Agent tools can access any file on the system |

### Configuration

```json
{
  "tools": {
    "restrict_to_workspace": true
  }
}
```

### Sandbox Status Levels

| Level | Description |
|-------|-------------|
| `system` | Enforced by OS sandbox (macOS App Sandbox, Bubblewrap) |
| `application` | Application-level guards only |
| `off` | No restriction |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENITE_CLAW_WORKSPACE_SANDBOX_PROVIDER` | Sandbox provider identifier |
| `AGENITE_CLAW_WORKSPACE_SANDBOX_ENFORCED` | Whether sandbox is enforced |

### Turn-Level Scope

The WebUI (`websocket` channel) uses scoped workspace from message/session metadata. Other channels use the default workspace.

### Validation

`validate_workspace_scope_payload()` ensures:
- Absolute path
- Existing directory
- No null bytes
- Valid access mode

## Exec Tool Security

The `exec` tool includes additional security measures:

### Deny/Allow Patterns

```json
{
  "tools": {
    "exec": {
      "deny_patterns": ["rm -rf", "sudo", "chmod 777"],
      "allow_patterns": ["ls", "cat", "git status"]
    }
  }
}
```

### Workspace Guards

Commands are executed within the workspace scope, preventing escape to parent directories.

### Timeout

Commands timeout after `tools.exec.timeout_seconds` (default: 120s).

## API Security

### Token Authentication

The WebSocket channel supports token-based authentication:

```json
{
  "channels": {
    "websocket": {
      "token": "your-secret-token",
      "websocket_requires_token": true
    }
  }
}
```

Tokens are short-lived (default: 300s TTL) and one-shot (consumed on validation).

### HMAC-Signed Media URLs

Media URLs in the WebUI are signed with HMAC-SHA256 to prevent unauthorized access.

### SVG CSP

SVGs are served with restrictive Content-Security-Policy:
```
Content-Security-Policy: default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox
```

## Email Security

### DKIM/SPF Verification

Email channel verifies sender authenticity:

```json
{
  "channels": {
    "email": {
      "verify_dkim": true,
      "verify_spf": true
    }
  }
}
```
