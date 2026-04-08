var p=document.querySelector('[data-panel-id="2"]');
var m=p&&(p.querySelector('[jsname="I2egDd"]')||p.querySelector('.hWX4r'));
if(m){
console.log("FOUND",m.tagName,m.className);
console.log("children:",m.children.length);
var q=m.querySelectorAll('[data-message-id],[information-message-id]');
console.log("msgs with id:",q.length);
for(var i=Math.max(0,q.length-3);i<q.length;i++){
var e=q[i];
console.log("---",i,e.tagName,e.className);
console.log("mid:",e.getAttribute("data-message-id"));
console.log("iid:",e.getAttribute("information-message-id"));
console.log("txt:",e.textContent.substring(0,150));
console.log("par:",e.parentElement.className);
}
console.log("---kids---");
for(var j=0;j<Math.min(m.children.length,5);j++){
var c=m.children[j];
console.log(j,c.tagName,c.className,c.children.length);
}
}else{console.log("not found")}
