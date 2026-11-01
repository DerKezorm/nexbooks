/**
 * A listener that counts every visit, for the test of the hostile book: the book tries to reach this address by every means
 * it knows (a picture, a style, a script, a frame, a form, a beacon). Not one visit may arrive. `GET /__hits` tells what came,
 * `GET /__reset` forgets it. Own port (18588), 127.0.0.1 only.
 */
import http from 'node:http'

const port = Number(process.env.CANARY_PORT ?? 18588)
let hits = []
http
  .createServer((request, response) => {
    if (request.url === '/health') {
      response.writeHead(200).end('ok')
      return
    }
    if (request.url === '/__hits') {
      response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(hits))
      return
    }
    if (request.url === '/__reset') {
      hits = []
      response.writeHead(200).end('ok')
      return
    }
    hits.push(`${request.method} ${request.url}`)
    response.writeHead(200, { 'access-control-allow-origin': '*', 'content-type': 'text/plain' }).end('x')
  })
  .listen(port, '127.0.0.1')
