package controller

import (
    "context"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "reflect"
    "strings"
    "time"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    corev1 "k8s.io/api/core/v1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    "k8s.io/apimachinery/pkg/api/meta"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/runtime/schema"
    "k8s.io/apimachinery/pkg/types"
    "k8s.io/client-go/tools/record"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
)

const Finalizer = "flyt.dev/cleanup"
const Managed = "hami-stage3-controller"
const Experiment = "flyt.dev/experiment"
const WorkerLabel = "flyt.dev/worker-uid"
const PlaneLabel = "flyt.dev/control-plane-uid"
const Period = 15 * time.Second
var VMIGVK = schema.GroupVersionKind{Group:"kubevirt.io", Version:"v1", Kind:"VirtualMachineInstance"}

type Base struct { client.Client; Reader client.Reader; Scheme *runtime.Scheme; Events record.EventRecorder }
func key(o client.Object) types.NamespacedName { return client.ObjectKeyFromObject(o) }
func workerName(uid string) string { return "fw-" + strings.ReplaceAll(uid,"-","") }
func planeName(p *api.FlytControlPlane) string { return "fcp-" + strings.ReplaceAll(string(p.UID),"-","") }
func identity(o client.Object) api.Reference { return api.Reference{Name:o.GetName(), UID:string(o.GetUID())} }
func vmi() *unstructured.Unstructured { u:= &unstructured.Unstructured{}; u.SetGroupVersionKind(VMIGVK); return u }
func retry() (ctrl.Result,error) { return ctrl.Result{RequeueAfter:Period},nil }
func objectHash(v interface{}) string { data,_:=json.Marshal(v); sum:=sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func (b *Base) status(ctx context.Context, o client.Object, phase, reason, message string, mutate func(*api.Status)) error {
    old:=o.DeepCopyObject().(client.Object)
    var status *api.Status
    switch x:=o.(type) {
    case *api.FlytWorker: status=&x.Status
    case *api.FlytGPUProfile: status=&x.Status
    case *api.FlytControlPlane: status=&x.Status
    default: return fmt.Errorf("unsupported status object")
    }
    status.Phase=phase; status.ObservedGeneration=o.GetGeneration()
    if mutate!=nil { mutate(status) }
    ready:=metav1.ConditionFalse
    if phase=="Ready" { ready=metav1.ConditionTrue }
    meta.SetStatusCondition(&status.Conditions,metav1.Condition{Type:"Ready",Status:ready,
        Reason:reason,Message:message,ObservedGeneration:o.GetGeneration()})
    if reflect.DeepEqual(old,o) { return nil }
    if err:=b.Status().Patch(ctx,o,client.MergeFrom(old)); err!=nil { return err }
    if b.Events!=nil { b.Events.Event(o,corev1.EventTypeNormal,reason,message) }
    return nil
}
func (b *Base) mark(ctx context.Context,o client.Object,phase,reason,msg string) (ctrl.Result,error) {
    err:=b.status(ctx,o,phase,reason,msg,nil); return ctrl.Result{RequeueAfter:Period},err
}
func (b *Base) finalizer(ctx context.Context,o client.Object,add bool) (bool,error) {
    has:=controllerutil.ContainsFinalizer(o,Finalizer)
    if has==add { return false,nil }
    if add && !o.GetDeletionTimestamp().IsZero() { return false,fmt.Errorf("cannot add finalizer during deletion") }
    old:=o.DeepCopyObject().(client.Object)
    if add { controllerutil.AddFinalizer(o,Finalizer) } else { controllerutil.RemoveFinalizer(o,Finalizer) }
    return true,b.Patch(ctx,o,client.MergeFromWithOptions(old,client.MergeFromWithOptimisticLock{}))
}
func (b *Base) workers(ctx context.Context, ns string) ([]api.FlytWorker,error) {
    list:=&api.FlytWorkerList{}; err:=b.Reader.List(ctx,list,client.InNamespace(ns)); return list.Items,err
}
func labels(owner client.Object, role string) map[string]string {
    m:=map[string]string{Experiment:Managed,"flyt.dev/role":role}
    switch x:=owner.(type) {
    case *api.FlytWorker: m[WorkerLabel]=string(x.UID); m[PlaneLabel]=x.Spec.ControlPlaneRef.UID
    case *api.FlytControlPlane: m[PlaneLabel]=string(x.UID)
    }
    return m
}
func ownedBy(o client.Object,owner client.Object) bool {
    for _,r:=range o.GetOwnerReferences() { if r.UID==owner.GetUID() && r.Controller!=nil && *r.Controller { return true } }
    return false
}
// Only create absent objects or repair objects already controlled by this exact
// CR UID. Extra labels/annotations are preserved; foreign same-name objects fail.
func (b *Base) ensure(ctx context.Context,owner client.Object,desired client.Object) error {
    desired.SetNamespace(owner.GetNamespace())
    if err:=controllerutil.SetControllerReference(owner,desired,b.Scheme); err!=nil { return err }
    desiredData,err:=json.Marshal(desired); if err!=nil{return err}
    var desiredMap map[string]interface{}; if err=json.Unmarshal(desiredData,&desiredMap);err!=nil{return err}
    delete(desiredMap,"status")
    if m,ok:=desiredMap["metadata"].(map[string]interface{});ok {
        for _,field:=range []string{"creationTimestamp","resourceVersion","uid","managedFields"}{delete(m,field)}
    }
    hash:=objectHash(desiredMap)
    annotations:=desired.GetAnnotations();if annotations==nil{annotations=map[string]string{}};annotations["flyt.dev/template-hash"]=hash;desired.SetAnnotations(annotations)
    current:=desired.DeepCopyObject().(client.Object)
    err=b.Reader.Get(ctx,key(desired),current)
    if apierrors.IsNotFound(err) { return b.Create(ctx,desired) };if err!=nil{return err}
    if !ownedBy(current,owner) { return fmt.Errorf("ownership conflict: %s",desired.GetName()) }
    if !current.GetDeletionTimestamp().IsZero() {return fmt.Errorf("resource terminating: %s",desired.GetName())}
    // Compare authored values, not just the hash annotation (which survives drift).
    raw,_:=json.Marshal(current); var currentMap map[string]interface{};_ = json.Unmarshal(raw,&currentMap)
    if current.GetAnnotations()["flyt.dev/template-hash"]==hash && subset(currentMap,desiredMap) {return nil}
    desired.SetResourceVersion(current.GetResourceVersion());desired.SetUID(current.GetUID())
    desired.SetFinalizers(current.GetFinalizers())
    extraLabels:=current.GetLabels();if extraLabels==nil{extraLabels=map[string]string{}};for k,v:=range desired.GetLabels(){extraLabels[k]=v};desired.SetLabels(extraLabels)
    extra:=current.GetAnnotations();if extra==nil{extra=map[string]string{}};for k,v:=range annotations{extra[k]=v};desired.SetAnnotations(extra)
    // Keep immutable Service allocation when repairing its desired fields.
    if d,ok:=desired.(*corev1.Service);ok {
        c:=current.(*corev1.Service);d.Spec.ClusterIP=c.Spec.ClusterIP;d.Spec.ClusterIPs=c.Spec.ClusterIPs
    }
    return b.Update(ctx,desired)
}
func subset(actual,desired interface{}) bool {
    switch d:=desired.(type) {
    case map[string]interface{}:
        a,ok:=actual.(map[string]interface{});if !ok{return false};for k,v:=range d{if !subset(a[k],v){return false}};return true
    case []interface{}:
        a,ok:=actual.([]interface{});if !ok||len(a)!=len(d){return false};for i:=range d{if !subset(a[i],d[i]){return false}};return true
    default:return reflect.DeepEqual(actual,desired)
    }
}
func (b *Base) deleteOwned(ctx context.Context,o client.Object,owner client.Object) error {
    if !ownedBy(o,owner){return fmt.Errorf("refusing foreign object %s",o.GetName())}
    uid,rv:=o.GetUID(),o.GetResourceVersion()
    err:=b.Delete(ctx,o,&client.DeleteOptions{Preconditions:&metav1.Preconditions{UID:&uid,ResourceVersion:&rv},PropagationPolicy:propagation()})
    return client.IgnoreNotFound(err)
}
func propagation() *metav1.DeletionPropagation { p:=metav1.DeletePropagationForeground;return &p }
