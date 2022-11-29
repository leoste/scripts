## gradient

Generates a gradient from one block to another within your selected area. Works in 2D and 3D. You can modify the command for different gradients.

Horizontal one axis one way: 
`
r=random()*2;
if(z+1<r){
` 

Horizontal same axis different way:
`
r=random()*2;
if(-z+1<r){
` 

Diagonal:
`
r=random()*4;
if(z+x+2<r){
`

3D:
`
r=random()*6;
if(z+x+y+3<r){
`