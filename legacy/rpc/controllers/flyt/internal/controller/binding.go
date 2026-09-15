package controller

import (
    "bufio"
    "context"
    "encoding/json"
    "fmt"
    "io"
    "net"
    "strings"
    "time"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    corev1 "k8s.io/api/core/v1"
    "k8s.io/apimachinery/pkg/types"
)

type Binding struct {
    WorkerUID string `json:"worker_uid"`
    VMIUID string `json:"vmi_uid"`
    VMIP string `json:"vm_ip"`
    PodUID string `json:"pod_uid"`
    PodIP string `json:"pod_ip"`
    Generation string `json:"generation"`
}
type Reply struct {
    OK bool `json:"ok"`
    Error string `json:"error"`
    Epoch string `json:"epoch"`
    Generation string `json:"generation"`
    Binding *Binding `json:"binding"`
    Bindings []Binding `json:"bindings"`
}
func (b *Base) binding(ctx context.Context,cp *api.FlytControlPlane,req map[string]interface{})(Reply,error){
    var reply Reply
    secret:=&corev1.Secret{}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:cp.Namespace,Name:cp.Spec.AuthSecretName},secret);err!=nil{return reply,err}
    token:=strings.TrimSpace(string(secret.Data["token"]))
    if len(token)<32||len(token)>256{return reply,fmt.Errorf("binding token length must be 32..256 bytes")}
    // Resolve the owned Service through the API, avoiding assumptions about the
    // operator Pod's DNS search path. This protocol stays inside the cluster.
    service:=&corev1.Service{}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:cp.Namespace,Name:planeName(cp)},service);err!=nil{return reply,err}
    if !ownedBy(service,cp)||net.ParseIP(service.Spec.ClusterIP)==nil{return reply,fmt.Errorf("manager Service identity unavailable")}
    dialer:=net.Dialer{Timeout:5*time.Second}
    conn,err:=dialer.DialContext(ctx,"tcp",net.JoinHostPort(service.Spec.ClusterIP,"12404"));if err!=nil{return reply,err};defer conn.Close()
    deadline:=time.Now().Add(90*time.Second);if d,ok:=ctx.Deadline();ok&&d.Before(deadline){deadline=d};_ = conn.SetDeadline(deadline)
    req["token"]=token
    if err=json.NewEncoder(conn).Encode(req);err!=nil{return reply,err}
    raw,err:=bufio.NewReader(io.LimitReader(conn,1<<20)).ReadBytes('\n');if err!=nil{return reply,err}
    if err=json.Unmarshal(raw,&reply);err!=nil{return reply,err}
    if !reply.OK{return reply,fmt.Errorf("binding request rejected: %s",reply.Error)}
    if reply.Epoch==""{return reply,fmt.Errorf("missing manager epoch")}
    return reply,nil
}
