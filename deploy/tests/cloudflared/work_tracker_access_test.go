// Copied into the PINNED upstream middleware package by cloudflared_access_test.sh.
// Real RSA signatures, JWKS over local TLS and NewJWTValidator; no fake JWT headers.
package middleware

import (
    "context"
    "crypto/rand"
    "crypto/rsa"
    "encoding/json"
    "net/http"
    "net/http/httptest"
    "testing"
    "time"

    "github.com/go-jose/go-jose/v4"
    "github.com/go-jose/go-jose/v4/jwt"
)

func TestWorkTrackerAccessBoundary(t *testing.T) {
    key, err := rsa.GenerateKey(rand.Reader, 2048)
    if err != nil { t.Fatal(err) }
    otherKey, err := rsa.GenerateKey(rand.Reader, 2048)
    if err != nil { t.Fatal(err) }
    jwk := jose.JSONWebKey{Key: &key.PublicKey, KeyID: "local-test", Algorithm: "RS256", Use: "sig"}
    jwksRequests := 0
    server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.URL.Path != "/testteam/cdn-cgi/access/certs" { t.Errorf("unexpected JWKS path: %s", r.URL.Path) }
        jwksRequests++
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(jose.JSONWebKeySet{Keys: []jose.JSONWebKey{jwk}})
    }))
    defer server.Close()
    oldTransport, oldURL := http.DefaultTransport, cloudflareAccessCertsURL
    http.DefaultTransport = server.Client().Transport
    cloudflareAccessCertsURL = server.URL + "/%s"
    defer func() { http.DefaultTransport, cloudflareAccessCertsURL = oldTransport, oldURL }()
    issuer := server.URL + "/testteam"
    validator := NewJWTValidator("testteam", "", []string{"work-tracker-audience"})
    for _, tc := range []struct {
        name, issuer, audience string
        expired, forged, missing bool
    }{
        {name: "valid", issuer: issuer, audience: "work-tracker-audience"},
        {name: "missing", missing: true},
        {name: "forged signature", issuer: issuer, audience: "work-tracker-audience", forged: true},
        {name: "expired", issuer: issuer, audience: "work-tracker-audience", expired: true},
        {name: "wrong audience", issuer: issuer, audience: "other-application"},
        {name: "wrong issuer team", issuer: server.URL + "/otherteam", audience: "work-tracker-audience"},
    } {
        t.Run(tc.name, func(t *testing.T) {
            request := httptest.NewRequest("POST", "https://work.example.invalid/api/auth/login", nil)
            if !tc.missing {
                signingKey := key
                if tc.forged { signingKey = otherKey }
                signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: signingKey},
                    (&jose.SignerOptions{}).WithType("JWT").WithHeader("kid", "local-test"))
                if err != nil { t.Fatal(err) }
                expiry := time.Now().Add(time.Hour)
                if tc.expired { expiry = time.Now().Add(-time.Hour) }
                token, err := jwt.Signed(signer).Claims(jwt.Claims{Issuer: tc.issuer,
                    Subject: "local-fixture-user", Audience: jwt.Audience{tc.audience},
                    Expiry: jwt.NewNumericDate(expiry), IssuedAt: jwt.NewNumericDate(time.Now().Add(-2*time.Hour))}).Serialize()
                if err != nil { t.Fatal(err) }
                request.Header.Set("Cf-Access-Jwt-Assertion", token)
            }
            result, err := validator.Handle(context.Background(), request)
            allowed := err == nil && result != nil && !result.ShouldFilterRequest
            if allowed != (tc.name == "valid") { t.Fatalf("allowed=%v error=%v", allowed, err) }
        })
    }
    if jwksRequests == 0 { t.Fatal("real remote JWKS retrieval was not exercised") }
    // Email eligibility is enforced by Access policy before issuance, NOT this verifier.
    // A real excluded identity is a mandatory Phase 9 test, never a local PASS.
}
