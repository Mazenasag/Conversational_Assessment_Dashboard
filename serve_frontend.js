const http=require('http');
const fs=require('fs');
const path=require('path');
const {spawn}=require('child_process');

const root=path.join(__dirname,'frontend');
const port=Number(process.env.PORT||8080);
const mime={
  '.html':'text/html; charset=utf-8',
  '.js':'text/javascript; charset=utf-8',
  '.css':'text/css; charset=utf-8',
  '.json':'application/json; charset=utf-8',
  '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.ico':'image/x-icon',
  '.png':'image/png',
  '.svg':'image/svg+xml'
};

const server=http.createServer((req,res)=>{
  let requestPath;
  try{requestPath=decodeURIComponent(new URL(req.url,'http://localhost').pathname)}catch{requestPath='/'}
  const relative=requestPath==='/'?'index.html':requestPath.replace(/^\/+/, '');
  const filePath=path.resolve(root,relative);
  if(filePath!==root&&!filePath.startsWith(root+path.sep)){
    res.writeHead(403);res.end('Forbidden');return;
  }
  fs.stat(filePath,(statError,stat)=>{
    if(statError||!stat.isFile()){
      res.writeHead(404,{'Content-Type':'text/plain; charset=utf-8'});res.end('Not found');return;
    }
    res.writeHead(200,{'Content-Type':mime[path.extname(filePath).toLowerCase()]||'application/octet-stream','Cache-Control':'no-store'});
    fs.createReadStream(filePath).pipe(res);
  });
});

server.listen(port,'127.0.0.1',()=>{
  const url=`http://localhost:${port}`;
  console.log(`JavaScript dashboard running at ${url}`);
  if(process.env.NO_OPEN==='1')return;
  const command=process.platform==='win32'?'cmd':process.platform==='darwin'?'open':'xdg-open';
  const args=process.platform==='win32'?['/c','start','',url]:[url];
  spawn(command,args,{detached:true,stdio:'ignore',windowsHide:true}).unref();
});
